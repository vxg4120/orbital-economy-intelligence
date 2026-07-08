# Orbital Economy Intelligence - Build Spec v2

**One-line pitch:** An open competitive-intelligence terminal for the orbital economy, built on a satellite identity graph: the entity-resolution, taxonomy, and normalization layer that cleanly answers "which physical object is this, who owns it right now, and what state is it in" across every public catalog that disagrees about the answer.

**Positioning shift from v1:** In v1 the benchmark metrics were the product. In v2 the **identity graph is the product; the metrics are the demo.** This mirrors the CannMenus architecture, where the normalization and joining layer was the most valuable asset and analytics rode on top of it.

**Elevator line for interviews:** "Everyone treats orbital data as a physics problem. I treated it as a master-data problem. One satellite is a NORAD number in Space-Track, a COSPAR designator, a commercial name like Starlink-30042, an ITU filing called USASAT-NGSO-3B, and a stale owner code in SATCAT. I built the crosswalk with temporal ownership and provenance, then ran market analytics on top. It is the same SKU-normalization problem I solved commercially at CannMenus, repointed at low Earth orbit."

---

## 1. Why this project wins

**For space hiring managers (Relativity, Rocket Lab, Epsilon3, Slingshot):** demonstrates real-time ingestion discipline, TimescaleDB at scale, and domain fluency (OMM vs TLE, catalog rollover, SATCAT quirks). Slingshot bought Seradata because curated satellite databases are a business; you are building the open, engineering-grade version of that asset.

**For generic data-platform roles:** "entity resolution," "master data management," "slowly changing dimensions," "data lineage/provenance," and "canonical taxonomy design" are literal JD keywords. One project feeds two of your three resumes.

**For the founder option:** the identity graph is the defensible moat of the "Headset for commercial space" concept. Building it as a portfolio piece keeps that door open at zero extra cost.

**Differentiation:** SSA incumbents (LeoLabs, Slingshot, COMSPOC, Kayhan) sell tracking and collision avoidance. Hobby projects do TLE anomaly detection. Nobody public does operator-vs-operator benchmarking on clean, provenance-tracked identities. The creative reframe is the whole pitch.

---

## 2. Hard constraints to design around (verified July 2026)

These are current, real, and each one is a credibility signal when handled correctly:

1. **The 5-digit catalog exhausts at 69999, estimated ~2026-07-12.** The count was at 69793 in early July 2026. New objects get 6-digit (eventually up to 9-digit) catalog numbers. **The legacy fixed-width TLE format cannot represent them.**
   - Consequence: build exclusively on **CCSDS OMM (Orbit Mean-Elements Message)** via JSON/CSV, never parse TLEs.
   - Consequence: **`BIGINT` for every NORAD ID column.** No exceptions. Mention this in the README; it dates the project as post-rollover-native.
2. **CelesTrak politeness is enforced, not advisory (as of 2026-03-26):**
   - GP data updates once every **2 hours**; one-download-per-update is enforced per IP.
   - More than ~100 MB/day risks the firewall.
   - Never pull `GROUP=active` and `GROUP=starlink` separately (Starlink is a subset of active). Pull the smallest group that covers your need.
   - `FORMAT` defaults to CSV as of 2026-05-09; request `FORMAT=json` explicitly.
3. **Space-Track suspends accounts for API hammering.** Batch queries with comma-delimited NORAD lists and `CREATION_DATE` windows. Respect documented request limits (on the order of <30/min, <300/hr; re-read the user agreement at signup, it changes).
4. **Redistribution rules differ by source.** The public repo ships **code, schema, and derived aggregates/screenshots**, never raw Space-Track dumps (user agreement restricts redistribution). CelesTrak: attribute, do not re-host bulk data. GCAT: CC-BY 4.0, cite Jonathan McDowell. UCS database: frozen since May 2023, cite as a historical seed.

**Architectural consequence of all four:** ingest once into TimescaleDB, then query only your own database for analysis. An `ingest_run` ledger plus conditional pull logic is a feature to showcase, not plumbing to hide.

---

## 3. Architecture: two layers

```
+--------------------------------------------------------------+
|  SURFACE: Superset dashboards / DQ report / optional MCP srv  |
+--------------------------------------------------------------+
|  METRICS: continuous aggregates + operator benchmark views    |
+--------------------------------------------------------------+
|  FACT LAYER (time series): gp_elements hypertable (OMM data)  |
+--------------------------------------------------------------+
|  IDENTITY GRAPH (dimensions): satellite, identifiers,         |
|  operators + hierarchy, temporal ownership, status taxonomy,  |
|  source assertions + conflict resolution + merge audit log    |
+--------------------------------------------------------------+
|  RAW: per-source landing tables + ingest_run ledger           |
+--------------------------------------------------------------+
```

The graph is the dimension layer; element sets are facts keyed by `norad_id` and joined to identity at query time. This is a star schema with slowly changing dimensions, which is exactly the vocabulary to use in interviews.

---

## 4. Data sources and endpoints

### 4.1 CelesTrak SATCAT (object catalog, the "SKU master")
- Bulk: `https://celestrak.org/pub/satcat.csv` (CSV format supports 9-digit catalog numbers). Pull at most daily.
- Query API: `https://celestrak.org/satcat/records.php?CATNR=...|INTDES=...|GROUP=...|NAME=...&FORMAT=json` with optional `PAYLOADS/ONORBIT/ACTIVE/MAX` flags.
- Gives: name, COSPAR, owner code, launch/decay dates, object type, RCS class, current apogee/perigee/period, operational status code.
- Known weaknesses (features for you): owner codes are country/org-coarse and go stale on M&A; names are uplink-style, not commercial.

### 4.2 CelesTrak GP (current element sets, OMM)
- `https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=json`
- One pull of `GROUP=active` per ingestion cycle (max every 2 h; for this project 1 to 2 pulls/day is plenty). Log every pull in `ingest_run`.

### 4.3 CelesTrak Supplemental GP (SupGP)
- Operator-supplied ephemerides (Starlink, OneWeb, Planet, Iridium, GLONASS, etc.).
- **Identity gold:** SupGP match results expose NO MATCH / mismatch / cross-tag flags between operator-declared objects and the catalog. Those flags are literally entity-resolution signals published by CelesTrak. Ingest the cross-tag anomalies as assertions.

### 4.4 Space-Track (historical depth)
- Auth: POST `https://www.space-track.org/ajaxauth/login` (free account; request it on day 1, approval can take a little time).
- Classes: `gp` (current), `gp_history` (backfill; window by `CREATION_DATE`), `satcat`, `decay`, `launch_site`.
- Use for: element-set history to compute the metrics, decay records for deorbit compliance.

### 4.5 GCAT (Jonathan McDowell's General Catalog)
- `https://planet4589.org/space/gcat/` TSV files. CC-BY 4.0.
- The scholarly counter-catalog: its own object IDs, its own status/phase taxonomy, often better ownership and program attribution than SATCAT, plus pre-catalog and analyst objects.
- This is your **second opinion source**; disagreements between SATCAT and GCAT are the seed corpus for the conflict report.

### 4.6 UCS Satellite Database (frozen May 2023)
- One-time seed for operator normalization: commercial operator names, users, purposes for ~7,500 satellites as of 2023. Never treat as current; treat as labeled training data for name matching and the operator table.

### 4.7 Operator hierarchy seed (manual YAML, ~2 hours of work)
- `operator_seed.yml`: canonical operators, aliases, and parentage with validity dates. Start with the known M&A chains: OneWeb -> Eutelsat (2023), Inmarsat -> Viasat (2023), Intelsat -> SES (2025 close), Sky Perfect JSAT consolidations, Planet's Terra Bella/BlackBridge lineage, Spire, Iridium, Globalstar, SpaceX/Starlink, Amazon/Kuiper.
- This file is your MSO tree. Reviewers can read it and instantly see the taxonomy thinking.

### 4.8 Deferred to Phase 3+ (do not block on these)
- ITU SNL/SNS filings (USASAT-style names): fiddly access, high identity value, add once the graph is stable.
- FCC IBFS/ELS filings: US market-access and call-sign layer.

### 4.9 Exoplanet stretch module (Section 10)
- NASA Exoplanet Archive TAP API and exoplanet.eu catalog.

---

## 5. Identity graph schema (Phase 1 core)

Design principles: surrogate keys everywhere; natural identifiers live in the crosswalk; every fact about ownership/status is time-bounded; every value carries source and provenance; merges are logged, never silent. All DDL targets PostgreSQL 17 + TimescaleDB.

```sql
-- Canonical physical object
CREATE TABLE satellite (
    satellite_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    norad_id         BIGINT UNIQUE,            -- BIGINT: 9-digit era. NULL until cataloged
    cospar_id        TEXT,                     -- e.g. '2023-054A'
    canonical_name   TEXT NOT NULL,
    object_type      TEXT NOT NULL DEFAULT 'UNKNOWN',  -- PAYLOAD | ROCKET_BODY | DEBRIS | UNKNOWN
    launch_date      DATE,
    decay_date       DATE,                     -- resolved value; conflicts live in assertions
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Identifier crosswalk: the heart of the graph
CREATE TABLE satellite_identifier (
    identifier_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    satellite_id     BIGINT NOT NULL REFERENCES satellite,
    id_type          TEXT NOT NULL,   -- norad | cospar | name_satcat | name_operator |
                                      -- name_gcat | gcat_id | ucs_row | itu_filing | fcc_callsign
    id_value         TEXT NOT NULL,
    valid_from       DATE,
    valid_to         DATE,            -- NULL = current
    source           TEXT NOT NULL,
    confidence       NUMERIC(3,2) NOT NULL DEFAULT 1.00,
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_type, id_value, source, satellite_id)
);
CREATE INDEX ON satellite_identifier (id_value);

-- Operators and their hierarchy (the MSO tree)
CREATE TABLE operator (
    operator_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name   TEXT NOT NULL UNIQUE,
    country          TEXT,
    operator_class   TEXT              -- commercial | civil | defense | academic | mixed
);

CREATE TABLE operator_alias (
    operator_id      BIGINT NOT NULL REFERENCES operator,
    alias            TEXT NOT NULL,
    source           TEXT NOT NULL,
    PRIMARY KEY (operator_id, alias, source)
);

CREATE TABLE operator_relationship (
    child_id         BIGINT NOT NULL REFERENCES operator,
    parent_id        BIGINT NOT NULL REFERENCES operator,
    relationship     TEXT NOT NULL,    -- subsidiary_of | brand_of | acquired_by | merged_into
    valid_from       DATE,
    valid_to         DATE,
    source           TEXT NOT NULL,
    PRIMARY KEY (child_id, parent_id, relationship, valid_from)
);

-- Temporal ownership: SCD Type 2, the OneWeb->Eutelsat problem
CREATE TABLE satellite_operator (
    satellite_id     BIGINT NOT NULL REFERENCES satellite,
    operator_id      BIGINT NOT NULL REFERENCES operator,
    role             TEXT NOT NULL,    -- owner | operator | manufacturer
    valid_from       DATE NOT NULL,
    valid_to         DATE,             -- NULL = current
    source           TEXT NOT NULL,
    confidence       NUMERIC(3,2) NOT NULL DEFAULT 1.00,
    PRIMARY KEY (satellite_id, operator_id, role, valid_from)
);

-- Canonical status taxonomy + per-source mappings
-- Canonical set: ACTIVE | PARTIAL | SPARE | INACTIVE | GRAVEYARD | DECAYED | UNKNOWN
CREATE TABLE status_mapping (
    source           TEXT NOT NULL,    -- satcat | gcat | ucs | supgp
    source_value     TEXT NOT NULL,    -- e.g. '+', 'P', 'B', GCAT phase codes
    canonical_status TEXT NOT NULL,
    notes            TEXT,
    PRIMARY KEY (source, source_value)
);
-- Populate from each source's current documentation during build;
-- do not trust third-party blog tables for the code meanings.

CREATE TABLE satellite_status_history (
    satellite_id     BIGINT NOT NULL REFERENCES satellite,
    canonical_status TEXT NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    source           TEXT NOT NULL,
    PRIMARY KEY (satellite_id, observed_at, source)
);

-- Field-level provenance: what each source claims, before resolution
CREATE TABLE source_assertion (
    assertion_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    satellite_id     BIGINT REFERENCES satellite,   -- NULL until matched
    attribute        TEXT NOT NULL,   -- owner | status | decay_date | object_type | name
    value            TEXT NOT NULL,
    source           TEXT NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    ingest_run_id    BIGINT NOT NULL
);
CREATE INDEX ON source_assertion (satellite_id, attribute);

-- Merge audit: no silent merges, ever
CREATE TABLE merge_log (
    merge_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surviving_id     BIGINT NOT NULL,
    merged_id        BIGINT NOT NULL,
    rule_fired       TEXT NOT NULL,   -- e.g. 'norad_exact', 'cospar+name_fuzzy>=0.92'
    score            NUMERIC(4,3),
    merged_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    details          JSONB
);

-- Ingestion ledger: politeness made visible
CREATE TABLE ingest_run (
    ingest_run_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source           TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    rows_ingested    INT,
    bytes_downloaded BIGINT,
    status           TEXT,            -- ok | skipped_fresh | error
    notes            TEXT
);
```

### Conflict resolution
Resolution is config, not code: a small precedence table (or YAML) per attribute, e.g. `decay_date: spacetrack_decay > satcat > gcat`, `owner: operator_seed > gcat > ucs > satcat`. The resolver reads assertions, applies precedence, writes the winner to the dimension tables, and leaves the losers queryable. The interview line: "disagreements are data, not errors."

### Match and merge rules (identity/match.py)
1. **Deterministic:** NORAD ID exact (authoritative); COSPAR launch+piece exact.
2. **Probabilistic (for name-only sources like UCS, ITU, press):** normalized-name similarity (casefold, strip punctuation, unify "Starlink-30042"/"STARLINK 30042" patterns) + launch-date proximity window + orbital-regime consistency (a GEO comsat cannot match a 550 km object) + country consistency. Score >= threshold auto-links with confidence < 1.0; borderline cases land in a human-review CSV.
3. Every auto-merge writes `merge_log` with the rule and score. This is your CannMenus dedupe discipline, on display.

---

## 6. Fact layer: element-set time series (Phase 2)

```sql
CREATE TABLE gp_elements (
    norad_id           BIGINT NOT NULL,
    epoch              TIMESTAMPTZ NOT NULL,
    mean_motion        DOUBLE PRECISION NOT NULL,  -- rev/day
    eccentricity       DOUBLE PRECISION NOT NULL,
    inclination        DOUBLE PRECISION,
    ra_of_asc_node     DOUBLE PRECISION,
    arg_of_pericenter  DOUBLE PRECISION,
    mean_anomaly       DOUBLE PRECISION,
    bstar              DOUBLE PRECISION,
    rev_at_epoch       BIGINT,
    source             TEXT NOT NULL,              -- celestrak_gp | spacetrack_gp_history | supgp
    creation_date      TIMESTAMPTZ,
    -- Derived (Earth mu = 398600.4418 km^3/s^2, Re = 6378.137 km).
    -- Postgres generated columns cannot reference each other, hence the repetition.
    semi_major_axis_km DOUBLE PRECISION GENERATED ALWAYS AS (
        power(398600.4418 / power(mean_motion * 2 * pi() / 86400.0, 2), 1.0/3.0)
    ) STORED,
    apogee_km          DOUBLE PRECISION GENERATED ALWAYS AS (
        power(398600.4418 / power(mean_motion * 2 * pi() / 86400.0, 2), 1.0/3.0)
        * (1 + eccentricity) - 6378.137
    ) STORED,
    perigee_km         DOUBLE PRECISION GENERATED ALWAYS AS (
        power(398600.4418 / power(mean_motion * 2 * pi() / 86400.0, 2), 1.0/3.0)
        * (1 - eccentricity) - 6378.137
    ) STORED,
    PRIMARY KEY (norad_id, epoch, source)
);
SELECT create_hypertable('gp_elements', 'epoch');
ALTER TABLE gp_elements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'norad_id',
    timescaledb.compress_orderby   = 'epoch'
);
SELECT add_compression_policy('gp_elements', INTERVAL '30 days');
```

**Continuous aggregates:** daily per-satellite stats keyed by `norad_id` only (caggs cannot join):

```sql
CREATE MATERIALIZED VIEW sat_daily
WITH (timescaledb.continuous) AS
SELECT norad_id,
       time_bucket('1 day', epoch) AS day,
       avg(semi_major_axis_km)      AS sma_avg,
       stddev_samp(semi_major_axis_km) AS sma_stddev,
       min(perigee_km)              AS perigee_min,
       max(apogee_km)               AS apogee_max,
       count(*)                     AS elset_count
FROM gp_elements
GROUP BY 1, 2;
```

Operator attribution happens **above** the cagg, in regular views that range-join `sat_daily` to `satellite_operator` on `day BETWEEN valid_from AND COALESCE(valid_to, 'infinity')`. Point out in the README that this is deliberate: identity churn (M&A) must not invalidate physics aggregates. That one sentence is a senior-engineer tell.

**Backfill plan:** `gp_history` for 6 to 8 benchmark operators (Starlink, OneWeb/Eutelsat, Planet, Spire, Iridium, ICEYE, Capella, Kuiper), windowed by `CREATION_DATE`, batched NORAD lists, well under documented request limits. Roughly 10 to 15M rows/year of history for that set; trivial under compression.

---

## 7. The four benchmark metrics (the demo)

All computed per operator via the identity graph, which is the point: without clean temporal ownership these numbers are wrong (OneWeb's 2024 performance belongs to Eutelsat, not OneWeb).

1. **Station-keeping tightness:** rolling 30-day stddev/IQR of `sma_avg` for ACTIVE payloads within a constellation shell. Lower = tighter ops; proxy for propulsion health and operational tempo.
2. **Time-to-operational:** days from launch to orbit acquisition (sma within a band of the constellation's shell median and stabilized for N consecutive days), measured off the orbit-raising curve.
3. **Deorbit compliance:** for payloads transitioning to INACTIVE/DECAYED, elapsed time from last-active to reentry vs the FCC 5-year rule; % compliant per operator. Regulatory-risk and ESG signal.
4. **Congestion exposure:** operator fleet distribution across altitude/inclination bins weighted by catalog object density per bin. A density-exposure proxy (real conjunction data is restricted); say so plainly in the README, honesty here reads as domain maturity.

---

## 8. The flagship artifact: the Data Quality and Conflict Report

A generated markdown/HTML report, refreshed by CI, front and center in the README. Contents:

- Objects where SATCAT and GCAT disagree on **status** (count + examples)
- Objects with conflicting **decay dates** across sources
- Owner attributions that are **stale post-M&A** (SATCAT code vs resolved operator)
- SupGP **cross-tag anomalies** (operator says X, catalog says Y)
- Match/merge stats: auto-linked, human-reviewed, unresolved
- Coverage: % of on-orbit payloads with resolved operator + status + provenance

This report is the screenshot for LinkedIn, the talking point for interviews, and the CannMenus 78%-to-96% story retold in a new domain. Title suggestion for an accompanying blog post: "Nobody agrees on who owns a satellite."

---

## 9. Surface (Phase 3)

- **Superset** dashboards (authentic to your stack, fastest path): operator benchmark comparisons, shell congestion heatmap, deorbit compliance league table, conflict report visuals.
- **Optional differentiator: an MCP server over the identity graph.** You shipped the cannabis industry's first MCP server; shipping "an MCP server for the satellite catalog" makes the two stories rhyme and is catnip for 2026 hiring managers. Read-only tools: `resolve_satellite`, `operator_fleet`, `benchmark_operators`, `conflicts_for_object`.
- Thin public page later only if job-search ROI justifies it; screenshots in the README are enough to start.

---

## 10. Exoplanet stretch module (passion garnish, 1 weekend max)

Prove the resolution engine is domain-agnostic by pointing it at a second sky-domain identity mess:

- Sources: NASA Exoplanet Archive TAP (`ps` / `pscomppars` tables) and exoplanet.eu CSV.
- The identity problem: host-star cross-IDs (HD, HIP, TIC, KIC, Gaia DR3) and planet-letter naming; the two archives famously disagree on the confirmed-planet count because their confirmation criteria differ.
- Reuse: generalize `satellite_identifier` to `entity_identifier(entity_type, ...)`; same assertion/conflict machinery; output a mini conflict report reconciling the count discrepancy.
- Framing in README: "same engine, different sky." One section, three screenshots, done. Do not let this delay Phases 1 to 3.

---

## 11. Repo structure

```
orbital-economy-intelligence/
  README.md                  # positioning, architecture diagram, DQ report, screenshots
  docker-compose.yml         # timescaledb (+ superset optional)
  db/migrations/             # numbered .sql files (schema above)
  ingest/
    celestrak_satcat.py
    celestrak_gp.py          # 2h-aware, one-download-per-update, ingest_run ledger
    spacetrack_client.py     # auth, CREATION_DATE-windowed gp_history backfill, backoff
    gcat_loader.py
    ucs_seed.py
    supgp_crosstags.py
  identity/
    normalize.py             # name normalization rules
    match.py                 # deterministic + probabilistic matchers
    merge.py                 # merge + merge_log
    resolve.py               # assertion -> dimension resolver (precedence config)
    operator_seed.yml        # the MSO tree
    status_map.yml           # per-source status mappings (verified against source docs)
  metrics/
    caggs.sql
    benchmark_views.sql
  quality/
    report.py                # generates the DQ and Conflict Report
  exoplanets/                # stretch module
  tests/                     # pytest: matchers, resolver precedence, derived-column math
  .github/workflows/ci.yml   # lint, tests, DQ report artifact
```

---

## 12. Phased roadmap with acceptance criteria

**Phase 0, this week (one evening + waiting on account approval):**
- Request Space-Track account; read the user agreement.
- Repo init, docker-compose TimescaleDB up, migrations applied.
- One polite pull each: SATCAT CSV, GCAT TSVs, UCS seed. Rows landed in raw tables; `ingest_run` shows it.
- Bonus timing: if the 69999 rollover happens mid-build (est. July 12), capture the first 6-digit object in the repo history. That commit is a story.

**Phase 1, weeks 1-2 (the centerpiece): the identity graph.**
- Loaders write `source_assertion` rows; matcher links SATCAT/GCAT/UCS objects; resolver populates dimensions with provenance.
- `operator_seed.yml` covers the major M&A chains; `status_map.yml` populated from source docs.
- **Acceptance:** every on-orbit payload resolves to a canonical satellite with operator, status, and provenance; conflict report v1 generates with real numbers; merge_log is non-empty and auditable.

**Phase 2, weeks 3-4: fact layer + metrics.**
- Daily GP ingestion running; gp_history backfilled for the benchmark operator set; caggs + benchmark views live.
- **Acceptance:** all four metrics computed per operator; at least one metric visibly changes when temporal ownership is applied vs naive SATCAT owner codes (that delta is a killer chart).

**Phase 3, week 5+: surface.**
- Superset dashboards, README screenshots, DQ report in CI. Optional MCP server.
- **Acceptance:** a stranger can understand the project from the README in 90 seconds.

**Stretch:** exoplanet module.

**Job-search integration:** when Phase 1 acceptance is met, swap the space resume's "(in development)" for the GitHub link and start the Tier 1 applications (Relativity data platform role first). Do not wait for Phase 3.

---

## 13. Resume and interview payload (draft now, fill numbers later)

Resume bullets (space + data-platform versions):
- "Built an open satellite identity graph resolving [N] tracked objects across NORAD, COSPAR, operator, and archival identifiers from four public catalogs, with temporal ownership (SCD2), a canonical status taxonomy, and per-attribute provenance"
- "Designed rate-limit-polite ingestion for the post-69999 catalog era on CCSDS OMM (JSON), with BIGINT-safe identifiers and an auditable ingestion ledger"
- "Benchmarked [K] operators on station-keeping, time-to-operational, and FCC 5-year deorbit compliance via TimescaleDB continuous aggregates over [M] historical element sets"

Interview mapping (memorize this table):
- Dispensary menus with no universal SKUs -> catalogs with no universal satellite identity
- Brand/MSO hierarchy -> operator hierarchy through M&A
- Menu-scrape politeness and SLAs -> CelesTrak 2h policy and ingest ledger
- 78% to 96% accuracy program -> the Conflict Report
- Cannabis industry's first MCP server -> satellite catalog MCP server

---

## 14. First 48 hours checklist

1. Request Space-Track account (do this first; approval lag).
2. `git init` + repo scaffold + docker-compose TimescaleDB.
3. Apply migrations (Section 5 DDL).
4. Pull SATCAT CSV once; land raw; log ingest_run.
5. Download GCAT TSVs once; land raw.
6. Draft `operator_seed.yml` for the top 15 operators and the three big M&A chains.
7. Read the SATCAT status-code and GCAT phase-code documentation; populate `status_map.yml` from the source docs.
