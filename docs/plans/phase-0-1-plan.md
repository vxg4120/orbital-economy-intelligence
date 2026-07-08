# Phase 0 + Phase 1 Implementation Plan — Orbital Economy Intelligence

Source spec: `docs/SPEC.md` (Build Spec v2). This plan covers Phase 0 (repo, DB, first polite pulls)
and Phase 1 (the identity graph + conflict report), ending at the Phase 1 acceptance criteria in
SPEC.md §12.

## Global Constraints (binding for every task)

- **BIGINT for every NORAD ID column. No exceptions.** (SPEC §2.1)
- **Never parse legacy TLE.** GP data is CCSDS OMM via `FORMAT=json` (explicit). SATCAT via bulk CSV. (SPEC §2.1, §2.2)
- **Politeness is enforced in code, not by convention:** every network pull goes through the
  `ingest_run` ledger; a pull is skipped with status `skipped_fresh` if a successful run for the
  same source+endpoint exists within that source's minimum interval (GP: 2 hours; SATCAT bulk: 24
  hours; GCAT: 24 hours). Never pull `GROUP=active` and any subgroup (e.g. starlink) in the same
  cycle. (SPEC §2.2)
- **Every network pull writes an `ingest_run` row** with bytes, rows, status (`ok | skipped_fresh | error`). (SPEC §5)
- **No raw source dumps committed to the repo.** `data/` is gitignored. The repo ships code,
  schema, and derived aggregates/reports only. Attribute CelesTrak; cite GCAT (CC-BY 4.0, Jonathan
  McDowell); cite UCS as a frozen historical seed. (SPEC §2.4)
- **Resolution precedence is config, not code** (YAML per attribute). (SPEC §5)
- **No silent merges:** every auto-merge/link writes `merge_log` with rule + score. (SPEC §5)
- **Status codes come from source documentation** (CelesTrak SATCAT status page, GCAT phases page),
  never from memory or third-party blogs. (SPEC §5 status_mapping comment)
- **Tests never hit the network.** HTTP is mocked (`responses`); small fixture files live in
  `tests/fixtures/`. DB-backed tests use the local TimescaleDB via `DATABASE_URL` and are marked
  `@pytest.mark.db`; they skip with a clear message when the DB is unreachable.
- Stack: Python 3.14 (venv + pip), `requests`, `psycopg[binary]` (v3), `PyYAML`, `pytest`,
  `responses`, `ruff`. DB: `timescale/timescaledb:latest-pg17` via docker compose, host port
  **5433** (avoids clashing with any local Postgres). Default
  `DATABASE_URL=postgresql://oei:oei@localhost:5433/oei`.
- **Documented deviations from SPEC DDL (apply them; they are deliberate):**
  1. `source_assertion` gains `source_key TEXT NOT NULL` — the source-native object key (SATCAT:
     NORAD id as text; GCAT: JCAT id; UCS: row hash/index). Without it, unmatched assertions
     (satellite_id NULL) are orphans that can never be attached after matching.
  2. `source_assertion.ingest_run_id` is a real FK to `ingest_run`.
  3. Assertion extraction lives in `identity/assertions.py` reading the raw landing tables, not
     inside the network loaders. Same data flow the spec describes ("loaders write source_assertion
     rows"), cleaner boundary: `ingest/` = network + landing, `identity/` = semantics.
- Local-only for now: no git remote, no pushes, no publishing.

---

## Task 1: Repo foundation (scaffold, docker-compose, tooling)

Create the project skeleton so every later task drops files into a known structure.

**Files to create:**

- `.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `data/`, `.env`, `.superpowers/`,
  `.pytest_cache/`, `.ruff_cache/`, `pgdata/`, `reports/scratch/`
- `docker-compose.yml`: single service `db`, image `timescale/timescaledb:latest-pg17`,
  container name `oei-db`, env `POSTGRES_DB=oei POSTGRES_USER=oei POSTGRES_PASSWORD=oei`, ports
  `5433:5432`, named volume for data, healthcheck via `pg_isready -U oei -d oei`.
- `requirements.txt`: `requests`, `psycopg[binary]`, `PyYAML`
- `requirements-dev.txt`: `-r requirements.txt`, `pytest`, `responses`, `ruff`
- `.env.example`: `DATABASE_URL=postgresql://oei:oei@localhost:5433/oei`,
  `SPACETRACK_IDENTITY=`, `SPACETRACK_PASSWORD=` (with a comment: request account at
  space-track.org; never commit real credentials)
- `common/__init__.py`, `common/db.py`: `get_conn()` returning a psycopg connection from
  `DATABASE_URL` env (default the local URL above), autocommit off by default, plus
  `get_autocommit_conn()` for DDL/migrations. Nothing else — no ORM, no pooling.
- `conftest.py` (repo root): defines the `db` pytest marker; fixture `db_conn` that connects via
  `common.db.get_conn()` and `pytest.skip("database not reachable at DATABASE_URL")` on failure;
  registers marker in `pytest.ini`/`pyproject.toml` section so output is warning-free.
- `pyproject.toml`: `[tool.pytest.ini_options]` (markers, testpaths=tests), `[tool.ruff]`
  (line-length 100, target py314). No build system / packaging — this is an app repo, modules are
  imported from the repo root.
- `Makefile` with targets: `venv` (create .venv + pip install dev reqs), `up` (docker compose up -d
  + wait for healthy), `down`, `psql` (psql into the container db), `migrate`
  (`.venv/bin/python scripts/migrate.py` — script arrives in Task 2; target may exist now),
  `test` (`.venv/bin/python -m pytest -q`), `lint` (`.venv/bin/ruff check .`).
- Directory placeholders with empty `__init__.py`: `ingest/`, `identity/`, `quality/`, `tests/`.
  Plus empty dirs kept via `.gitkeep`: `db/migrations/`, `metrics/`, `data/` (gitignored content),
  `docs/reports/`.
- `tests/test_smoke.py`: imports `common.db`, asserts default URL parsing works (no DB needed).

**Verification (run all):**
1. `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt` succeeds.
2. `docker compose up -d` then wait until `docker inspect --format '{{.State.Health.Status}}' oei-db`
   is `healthy`.
3. `.venv/bin/python -m pytest -q` → smoke test passes, zero warnings.
4. `.venv/bin/ruff check .` clean.

Commit when green.

---

## Task 2: Database schema — migrations + runner

Implement the full schema from SPEC §5 and §6 as numbered SQL migrations plus a tiny runner.
The DDL in SPEC.md is the authority — transcribe it (with the plan's documented deviations).

**Files to create:**

- `scripts/migrate.py`: applies `db/migrations/*.sql` in filename order on an **autocommit**
  connection (TimescaleDB DDL like `create_hypertable` and continuous aggregates cannot run inside
  a transaction block); records applied filenames in `schema_migrations(filename TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ DEFAULT now())`; skips already-applied files; prints what it did. Simple,
  idempotent, re-runnable. Split each file on statements safely by executing the whole file content
  in one `execute()` call per file EXCEPT files containing continuous aggregates — simplest robust
  rule: execute file content as a single script; psycopg allows multiple statements per execute on
  autocommit connections.
- `db/migrations/0001_extensions.sql`: `CREATE EXTENSION IF NOT EXISTS timescaledb;`
- `db/migrations/0002_ingest_ledger.sql`: `ingest_run` exactly as SPEC §5.
- `db/migrations/0003_raw.sql`: per-source landing tables. All raw tables get
  `ingest_run_id BIGINT NOT NULL REFERENCES ingest_run` and `loaded_at TIMESTAMPTZ NOT NULL
  DEFAULT now()`.
  - `raw_satcat`: typed columns for the CelesTrak SATCAT CSV header (OBJECT_NAME, OBJECT_ID,
    NORAD_CAT_ID **BIGINT**, OBJECT_TYPE, OPS_STATUS_CODE, OWNER, LAUNCH_DATE DATE, LAUNCH_SITE,
    DECAY_DATE DATE, PERIOD NUMERIC, INCLINATION NUMERIC, APOGEE NUMERIC, PERIGEE NUMERIC, RCS,
    DATA_STATUS_CODE, ORBIT_CENTER, ORBIT_TYPE — snake_cased), PK `(norad_cat_id, ingest_run_id)`.
  - `raw_gcat_satcat`: typed columns for the fields identity needs — `jcat TEXT`, `norad_id BIGINT`
    (GCAT "Satcat" column), `piece TEXT`, `object_type TEXT` (GCAT "Type"), `name TEXT`,
    `pl_name TEXT`, `launch_date TEXT` (GCAT vague-date format, keep raw), `decay_date TEXT`
    (GCAT "DDate", vague format), `status TEXT`, `dest TEXT`, `owner TEXT`, `state TEXT`,
    `manufacturer TEXT`, `bus TEXT`, `mass TEXT`, `perigee_km NUMERIC NULL`, `apogee_km NUMERIC
    NULL`, `inc_deg NUMERIC NULL`, `op_orbit TEXT`, `alt_names TEXT`, plus `extra JSONB NOT NULL
    DEFAULT '{}'` holding every other column from the TSV keyed by original header name. PK
    `(jcat, ingest_run_id)`.
  - `raw_gcat_psatcat`: same pattern for GCAT's payload catalog psatcat.tsv: `jcat TEXT`,
    `piece TEXT`, `name TEXT`, plus `extra JSONB` for everything else; PK `(jcat, ingest_run_id)`.
  - `raw_ucs`: `row_key TEXT` (stable hash of the row), `name TEXT`, `country_operator TEXT`,
    `operator TEXT`, `users TEXT`, `purpose TEXT`, `norad_id BIGINT NULL`, `cospar_id TEXT NULL`,
    `launch_date TEXT`, `extra JSONB`; PK `(row_key, ingest_run_id)`.
  - `raw_supgp_status`: `norad_id BIGINT NULL`, `object_name TEXT`, `file_tag TEXT` (which SupGP
    constellation file), `flag TEXT` (e.g. NO_MATCH, CROSS_TAG), `detail TEXT`, PK generated
    identity col.
- `db/migrations/0004_identity.sql`: SPEC §5 DDL verbatim — `satellite`, `satellite_identifier`
  (+ its index), `operator`, `operator_alias`, `operator_relationship`, `satellite_operator`,
  `status_mapping`, `satellite_status_history`, `source_assertion` (**with the two deviations:**
  add `source_key TEXT NOT NULL` after `satellite_id`, and make `ingest_run_id BIGINT NOT NULL
  REFERENCES ingest_run`), `merge_log`, plus the `source_assertion (satellite_id, attribute)`
  index and an additional index on `source_assertion (source, source_key)`.
- `db/migrations/0005_fact.sql`: SPEC §6 `gp_elements` DDL verbatim (generated columns included),
  then `create_hypertable`, compression settings, compression policy — exactly as SPEC §6.
  Guard idempotency: `create_hypertable('gp_elements','epoch', if_not_exists => TRUE)`.

  NOTE: `sat_daily` continuous aggregate is NOT a migration; it lives in `metrics/caggs.sql`
  (Task 5) per the repo structure in SPEC §11.

**Tests (`tests/test_migrations.py`, all `@pytest.mark.db`):**
- Runner applies cleanly on the running dev DB; second run is a no-op (asserts via
  `schema_migrations` count unchanged).
- `gp_elements` is a hypertable (query `timescaledb_information.hypertables`).
- Insert an ISS-like row (`mean_motion=15.5, eccentricity=0.0004`) with a dummy ingest — assert
  `semi_major_axis_km` ≈ 6795 ± 15 km and `perigee_km < apogee_km`.
- `source_assertion` insert without valid `ingest_run_id` fails (FK enforced).
- All NORAD columns: assert `information_schema.columns.data_type = 'bigint'` for every column
  named like `%norad%` across all tables (the BIGINT constraint, as a test).

**Verification:** `make migrate` against the Task 1 container; `make test` green; `make lint` clean.
Commit when green.

---

## Task 3: Ingestion layer (network + landing, politeness enforced)

Build `ingest/` per SPEC §4 + §11. All modules share one politeness/ledger core. No semantic
interpretation here — land data raw; identity semantics happen in Task 4.

**Files to create:**

- `ingest/runlog.py`: the ledger core.
  - `start_run(conn, source, endpoint) -> run_id`, `finish_run(conn, run_id, rows, bytes_dl,
    status, notes=None)`.
  - `fresh_within(conn, source, endpoint, interval) -> bool`: True if a run with status `ok`
    for the same source+endpoint finished within `interval`.
  - `polite_get(conn, source, endpoint, url, min_interval, **requests_kwargs)`: checks
    `fresh_within` → if fresh, writes a `skipped_fresh` run row and returns None; otherwise
    performs `requests.get` (timeout=120, `User-Agent: orbital-economy-intelligence/0.1
    (portfolio project; polite; contact in repo)`), records bytes, returns the response, and on
    HTTP error writes an `error` run row and raises.
- `ingest/celestrak_satcat.py`: `run(conn)` — `polite_get` on
  `https://celestrak.org/pub/satcat.csv`, min interval 24h. Parse CSV **by header name** (stdlib
  `csv.DictReader`), snake_case the headers, coerce NORAD to int (BIGINT column), empty strings →
  NULL, dates parsed. Load into `raw_satcat` tagged with this run's id (plain INSERT; history of
  snapshots is fine and cheap). Also saves the raw payload to `data/celestrak/satcat-<date>.csv`
  (gitignored) for reproducibility.
- `ingest/celestrak_gp.py`: `run(conn, group='active')` — `polite_get` on
  `https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=json`, min interval 2h.
  Parse OMM JSON list; INSERT into `gp_elements` with `source='celestrak_gp'`,
  `ON CONFLICT DO NOTHING` (PK norad/epoch/source). Field mapping: NORAD_CAT_ID→norad_id,
  EPOCH→epoch (UTC), MEAN_MOTION, ECCENTRICITY, INCLINATION, RA_OF_ASC_NODE, ARG_OF_PERICENTER,
  MEAN_ANOMALY, BSTAR, REV_AT_EPOCH, CREATION_DATE. Never request any group other than the one
  asked; default and only default is `active`.
- `ingest/gcat_loader.py`: `run(conn)` — two `polite_get`s (min interval 24h):
  `https://planet4589.org/space/gcat/tsv/cat/satcat.tsv` → `raw_gcat_satcat`, and
  `https://planet4589.org/space/gcat/tsv/cat/psatcat.tsv` → `raw_gcat_psatcat`.
  GCAT TSVs: first line is the header and starts with `#`; parse header dynamically; store the
  typed subset (see Task 2 schema) + everything else in `extra` JSONB. GCAT uses `-` for
  missing values → NULL. GCAT "Satcat" column → `norad_id` BIGINT (may be absent for
  analyst objects → NULL). Save raw files under `data/gcat/`.
- `ingest/ucs_seed.py`: `run(conn, path_or_url=None)` — the UCS database is frozen (May 2023) and
  its hosting URL is unstable; accept a local file path first (`data/ucs/*.xlsx|.txt|.csv`) or an
  explicit URL argument. Parse the tab-separated `.txt`/csv export (header names like "Name of
  Satellite, Alternate Names", "Country of Operator/Owner", "Operator/Owner", "Users", "Purpose",
  "NORAD Number", "COSPAR Number", "Date of Launch"). Land into `raw_ucs` with `row_key` =
  sha1(name+cospar). If no file and no URL → log a clear message and return without error (it is
  an optional seed). No xlsx dependency — support the TSV/CSV export only, document that.
- `ingest/spacetrack_client.py`: class `SpaceTrackClient` — session login via POST
  `https://www.space-track.org/ajaxauth/login` with `identity`/`password` from env
  (`SPACETRACK_IDENTITY`, `SPACETRACK_PASSWORD`); raises a clear error if unset. Rate limiter:
  min 3s between requests (well under 30/min), exponential backoff (base 30s, x2, max 3 retries)
  on 429/5xx. Methods:
  - `gp_history(norad_ids: list[int], created_since: str, created_before: str)` — batched
    comma-delimited NORAD lists (batches of 100), windowed by `CREATION_DATE`, `format=json`,
    yields parsed rows; caller lands them into `gp_elements` with `source='spacetrack_gp_history'`
    via `land_gp_history(conn, rows)` helper in the same module.
  - `decay(norad_ids)` — decay messages, `format=json`, returns rows (landing deferred to
    Phase 2; keep the method thin).
  Every fetch runs through the same `ingest_run` ledger (`source='spacetrack'`).
- `ingest/supgp_crosstags.py`: best-effort. `run(conn)` — fetch
  `https://celestrak.org/NORAD/elements/supplemental/` (min interval 24h), parse any rows/flags
  indicating NO MATCH / cross-tag anomalies from the index tables into `raw_supgp_status`. The
  page structure is not guaranteed: parse defensively; if nothing parseable found, land zero rows
  with an `ok` run and a note. Tests use a saved fixture HTML.
- `scripts/ingest_all.py`: CLI (`python scripts/ingest_all.py [--source satcat|gp|gcat|ucs|supgp|all]`)
  running the loaders in order satcat → gcat → gp → supgp → ucs, each in its own try/except so one
  failure doesn't stop the rest; prints the resulting `ingest_run` rows as a table at the end.

**Tests (`tests/test_ingest_*.py`, network mocked with `responses`, DB tests marked `db`):**
- Ledger: `polite_get` on a fresh endpoint fetches and logs `ok`; immediate second call logs
  `skipped_fresh` and performs **zero** HTTP calls (assert via responses call count); HTTP 500
  logs `error` and raises.
- SATCAT: fixture CSV (5 rows incl. one 6-digit NORAD id like 100001 and one decayed object) lands
  correctly typed; empty strings → NULL; date parsing.
- GP: fixture OMM JSON (3 rows) lands into `gp_elements`; re-run same fixture → no duplicate rows
  (ON CONFLICT); generated columns populated.
- GCAT: fixture TSV with `#`-prefixed header, `-` missing values, one row without a Satcat number
  → lands with `norad_id` NULL and full `extra` JSONB.
- Space-Track client: login flow mocked; batching (150 ids → 2 requests); backoff on 429 (mock
  time.sleep); missing creds → clear error.
- UCS: fixture TSV lands; missing file → graceful no-op.

**Verification:** `make test` green (unit + db), `make lint` clean. NO live network calls in this
task — live pulls happen in Task 6. Commit when green.

---

## Task 4: Identity graph engine (the centerpiece)

Build `identity/` per SPEC §5: normalize → match → merge → assertions → resolve, plus the two
curated YAML seeds. This is the CannMenus-normalization-layer analog and the core of the project.

**Files to create:**

- `identity/normalize.py`:
  - `norm_name(s)` — casefold, strip punctuation to spaces, collapse whitespace, unify
    constellation-numbering patterns (`STARLINK-30042` / `Starlink 30042` / `STARLINK 30042 (v2)`
    → `starlink 30042`), strip parenthetical suffixes, strip trailing designator noise.
  - `norm_cospar(s)` — normalize COSPAR/international designator to `YYYY-NNNP` form (SATCAT
    `OBJECT_ID` "2023-054A" and GCAT piece formats both normalize to the same string; handle
    GCAT's occasional `1998-067A` style already-clean values and pre-1963 formats by passing
    through unchanged with a flag).
  - `orbital_regime(perigee_km, apogee_km)` → `LEO | MEO | GEO | HEO | UNKNOWN`
    (LEO: apogee < 2000; GEO: 35786±500 and low eccentricity implied by perigee also in band;
    MEO between; HEO: perigee < 2000 < apogee).
- `identity/match.py`:
  - Deterministic pass: group raw records (latest snapshot per source) by exact NORAD id → link;
    for records without NORAD, by normalized COSPAR → link.
  - Probabilistic pass (for NORAD-less/COSPAR-less rows, e.g. UCS rows with neither): candidate
    generation by normalized-name similarity (stdlib `difflib.SequenceMatcher.ratio`), gated by
    launch-date proximity (±30 days when both known) and orbital-regime consistency and
    country/state consistency when known. Score = weighted combo (name 0.6, launch 0.25, regime
    0.15 — weights in `identity/match_config.yml`, threshold auto-link ≥ 0.92, review band
    0.75–0.92 → append to `data/review/match_review.csv` with both records and score).
  - Output: `satellite` rows created/updated, `satellite_identifier` crosswalk rows (id_type per
    SPEC: `norad`, `cospar`, `name_satcat`, `name_gcat`, `gcat_id`, `ucs_row`), each with source
    + confidence.
- `identity/merge.py`: `link(conn, satellite_id, raw_ref, rule, score, details)` writes crosswalk
  + `merge_log` in one transaction. When two existing satellite rows are discovered to be the same
  physical object: `merge(conn, surviving_id, merged_id, rule, score)` — repoint identifiers,
  assertions, operator links, status history; write `merge_log`; delete the merged shell row.
  **Every** link/merge writes `merge_log` — no silent writes anywhere in identity/.
- `identity/assertions.py`: extract per-attribute claims from the latest raw snapshot of each
  source into `source_assertion` (attribute ∈ owner | status | decay_date | object_type | name),
  with `source_key` (satcat: norad id text; gcat: jcat; ucs: row_key), `satellite_id` filled via
  the crosswalk (NULL if unmatched), `observed_at` = the raw row's ingest time, `ingest_run_id`
  = the raw row's run. Idempotent per (source, source_key, attribute, ingest_run) — re-runs don't
  duplicate.
- `identity/resolve.py`: reads `identity/precedence.yml`
  (`owner: [operator_seed, gcat, ucs, satcat]`, `status: [gcat, satcat, ucs]`,
  `decay_date: [spacetrack_decay, satcat, gcat]`, `object_type: [gcat, satcat]`,
  `name: [ucs, gcat, satcat]` — commercial names beat uplink names) and writes winners to the
  dimension tables (`satellite.canonical_name/object_type/decay_date`,
  `satellite_status_history` via `status_mapping`, `satellite_operator`). Losers stay queryable in
  `source_assertion` — disagreements are data, not errors.
  - Status: source value → canonical via `status_mapping` table (seeded from
    `identity/status_map.yml` by `scripts/build_graph.py`); unmapped source values resolve to
    UNKNOWN **and are counted/logged** (they feed the DQ report).
  - Owner → operator: match assertion owner strings/codes against `operator` + `operator_alias`
    (seeded from `identity/operator_seed.yml`). Unmatched owner values remain unresolved and
    counted (DQ report input).
  - **Temporal ownership (SCD2):** when the resolved operator has an
    `operator_relationship` (`acquired_by`/`merged_into`) with `valid_from = D`, and the
    satellite's launch_date < D: write two `satellite_operator` rows — (child, launch→D) and
    (parent, D→NULL). Otherwise one open-ended row from launch date. This is the
    OneWeb→Eutelsat showcase; get it right and tested.
- `identity/operator_seed.yml`: canonical operators (≥15), aliases (including SATCAT owner codes
  — fetch the code list from https://celestrak.org/satcat/sources.php to get codes for these
  operators), country, class, and the M&A chains with dates:
  OneWeb → Eutelsat (Eutelsat Group) 2023; Inmarsat → Viasat 2023; Intelsat → SES 2025;
  SpaceX (Starlink); Amazon (Kuiper); Planet Labs (incl. Terra Bella and BlackBridge lineage);
  Spire; Iridium; Globalstar; ICEYE; Capella; Telesat; EchoStar/Hughes; Sky Perfect JSAT;
  Eutelsat; SES; Viasat. Schema of the YAML: `operators: [{name, country, class, aliases: [],
  satcat_codes: [], gcat_codes: []}]`, `relationships: [{child, parent, relationship,
  valid_from}]`. Verify M&A dates against public record before writing.
- `identity/status_map.yml`: per-source mappings to the canonical set
  `ACTIVE | PARTIAL | SPARE | INACTIVE | GRAVEYARD | DECAYED | UNKNOWN`.
  - `satcat`: fetch and read https://celestrak.org/satcat/status.php — map each documented
    OPS_STATUS_CODE. Include a `notes` line per code quoting the source doc's meaning.
  - `gcat`: fetch and read https://planet4589.org/space/gcat/web/intro/phases.html — map the
    Status codes that appear in satcat.tsv/psatcat.tsv (orbit/reentry/landing/attached codes).
  - `ucs`: UCS rows are all "operational as of the frozen date" → map to ACTIVE with a note
    about staleness.
- `scripts/build_graph.py`: the Phase 1 pipeline CLI: seed operators + status_mapping from YAML
  (idempotent upserts) → match (deterministic then probabilistic) → assertions → resolve → print
  summary counts (satellites, identifiers by type, merge_log rows, assertions by source, resolved
  coverage %, unmapped status values, unmatched owners, review-queue size).

**Tests (`tests/test_identity_*.py` — unit tests for pure logic, `db`-marked for pipeline):**
- normalize: Starlink pattern table-driven cases; COSPAR forms; regime boundaries (GEO band edges).
- match: deterministic NORAD/COSPAR linking on synthetic raw rows; probabilistic: true-positive
  (same sat, name variant + close launch), true-negative (GEO comsat vs LEO cubesat with similar
  names → blocked by regime gate), borderline → review CSV, ≥0.92 → auto-link with merge_log row.
- merge: merging two satellites repoints all child rows and logs; no orphans left (FK check).
- resolve: precedence honored per attribute (fixture assertions from 3 sources; winner matches
  precedence.yml); unmapped status → UNKNOWN + counted; SCD2 acquisition split produces the two
  expected rows for a pre-acquisition launch and one row for post-acquisition launch.
- YAML seeds: schema-validate both files load and every `relationships.child/parent` exists in
  `operators`; every canonical status used in status_map.yml is in the canonical enum.
- Full pipeline (db): tiny synthetic raw_satcat + raw_gcat_satcat fixture (5 objects: 3 matched by
  NORAD with one status disagreement, 1 GCAT-only analyst object, 1 SATCAT-only) → build_graph →
  assert satellite count 5, crosswalk rows present, disagreement visible in source_assertion,
  merge_log non-empty, coverage summary correct.

**Verification:** `make test` green, `make lint` clean. Commit when green.

---

## Task 5: Metrics SQL + Data Quality & Conflict Report

The demo layer (SPEC §6 caggs, §7 metrics as far as computable pre-backfill) and the flagship DQ
report (SPEC §8).

**Files to create:**

- `metrics/caggs.sql`: `sat_daily` continuous aggregate exactly as SPEC §6
  (`CREATE MATERIALIZED VIEW IF NOT EXISTS ... WITH (timescaledb.continuous)`), plus a refresh
  policy (`add_continuous_aggregate_policy`, 1h schedule, `if_not_exists => TRUE`).
- `metrics/benchmark_views.sql` (all `CREATE OR REPLACE VIEW`):
  - `v_sat_operator_daily`: `sat_daily` → `satellite` (by norad_id) → range-join
    `satellite_operator` (`day::date BETWEEN valid_from AND COALESCE(valid_to,'infinity')`,
    role='owner') → `operator`. The README sentence about identity churn not invalidating physics
    aggregates lives on this view (comment in the SQL).
  - `v_station_keeping_30d`: per satellite, 30-day rolling stddev of `sma_avg` (window over
    `v_sat_operator_daily`), then per-operator aggregate for ACTIVE payloads (status from latest
    `satellite_status_history`).
  - `v_congestion_exposure`: altitude bins (50 km) × inclination bins (5°) built from the latest
    element set per object in `gp_elements`; per-bin object density; per-operator exposure =
    fleet-weighted density share. Works with current GP data only (no history needed).
  - `v_deorbit_compliance`: skeleton per SPEC §7.3 — for satellites with canonical status
    DECAYED/INACTIVE, elapsed days between last ACTIVE observation in `satellite_status_history`
    and `satellite.decay_date`, compliant = ≤ 5 years. Will be sparse until Phase 2 backfill;
    that's expected and documented in a comment.
  - Time-to-operational is Phase 2 (needs gp_history); add a SQL comment placeholder, no view.
- `scripts/apply_metrics.py`: applies both SQL files idempotently on autocommit (cagg files must
  not run in a transaction); add Makefile target `metrics`.
- `quality/report.py`: generates `docs/reports/dq_report.md` (and is safe to re-run):
  1. Header: generated-at timestamp, ingest_run summary table (last run per source/status).
  2. **Status disagreements** SATCAT vs GCAT: same satellite_id, different canonical_status from
     the two sources' latest assertions (count + 10 example rows: norad, name, satcat says, gcat
     says).
  3. **Decay-date conflicts** across sources (count + 10 examples with both dates).
  4. **Stale post-M&A owners**: satellites whose SATCAT owner assertion maps (via alias) to an
     operator that has an `acquired_by/merged_into` relationship active before today, i.e. the
     catalog still names the child (count + examples; the OneWeb/Eutelsat, Inmarsat/Viasat,
     Intelsat/SES corpus).
  5. **SupGP cross-tag anomalies** from `raw_supgp_status` (count + examples, or "no data yet").
  6. **Match/merge stats**: crosswalk rows by id_type, merge_log by rule_fired, review-queue size,
     unmatched-by-source counts.
  7. **Coverage**: % of on-orbit payloads (not DECAYED) with resolved operator; % with
     non-UNKNOWN status; % with ≥2 source identifiers (the "graph vs list" number).
  Pure SQL + string formatting; no plotting deps.
- Makefile target `report`: `python quality/report.py`.

**Tests:**
- `tests/test_metrics.py` (`db`): apply caggs+views on the dev DB (after migrations); insert
  synthetic gp rows for 2 fake satellites across 3 days + operator links (one with an
  acquisition mid-window) → `v_sat_operator_daily` attributes days before/after the acquisition
  to different operators (the killer-chart mechanic, as a test); congestion view returns bins.
- `tests/test_quality_report.py` (`db`): seed the Task 4 synthetic fixture, run report → file
  exists, contains the status-disagreement section with the planted disagreement, coverage
  percentages parse as numbers.

**Verification:** `make metrics && make report` on dev DB; `make test` green; `make lint` clean.
Commit when green.

---

## Task 6: Phase 0 live pulls, end-to-end Phase 1 build, CI, README

Bring it alive with real data (this task DOES hit the network — once, politely), wire CI, write
the real README.

**Steps:**

1. **Live pulls** (each exactly once, via `scripts/ingest_all.py`): SATCAT CSV, GCAT satcat.tsv +
   psatcat.tsv, GP `GROUP=active` (one pull), SupGP index (best-effort), UCS (expected: graceful
   skip unless a local file exists — fine). Verify `ingest_run` rows and raw-table counts
   (`raw_satcat` should be ≥ 60,000 rows — full catalog including decayed; `raw_gcat_satcat`
   similar order; `gp_elements` ≥ 10,000).
2. **Live identity build**: `scripts/build_graph.py` on the real data. Expect ≥ 95% of SATCAT
   objects linked to GCAT by NORAD; real status disagreements > 0; merge_log non-empty; unmatched
   owners list exists (that's the curation backlog, not a failure).
3. **Metrics + report on real data**: `make metrics && make report` → commit the generated
   `docs/reports/dq_report.md` (real numbers — this is the flagship artifact).
4. `.github/workflows/ci.yml`: on push/PR — python 3.14 setup, pip install dev reqs, ruff, unit
   tests (non-db), then a `db` job with a `timescale/timescaledb:latest-pg17` service container:
   migrate + metrics + full pytest + generate DQ report from the synthetic fixture and upload as
   artifact. (File only; repo is local-only for now — CI runs whenever it's eventually pushed.)
5. **README.md** (rewrite): one-line pitch; the elevator paragraph (SPEC §0); architecture diagram
   (SPEC §3 ASCII); "why BIGINT / post-69999" note; politeness design + ingest_run ledger;
   identity graph description with the deviations rationale; DQ report link + headline numbers;
   quickstart (`make venv up migrate`, `python scripts/ingest_all.py`, `python
   scripts/build_graph.py`, `make metrics report`); data-source attribution block (CelesTrak
   attribution, GCAT CC-BY-4.0 Jonathan McDowell citation, UCS frozen-seed citation, Space-Track
   redistribution note: no raw dumps in repo); roadmap (Phases 2-3 + exoplanet stretch); license
   note (code MIT, data stays out of repo).
6. Update `tasks/todo.md`: mark done items, add Review section (per repo owner's global workflow).

**Acceptance (Phase 0 + Phase 1 from SPEC §12):**
- `ingest_run` shows ok runs for satcat, gcat, gp.
- Every on-orbit payload resolves to a canonical satellite; coverage numbers printed and in the
  DQ report.
- Conflict report v1 committed with real numbers; merge_log non-empty and auditable.
- `make test` fully green; `make lint` clean.

Commit when green. Do NOT push anywhere (local only).
