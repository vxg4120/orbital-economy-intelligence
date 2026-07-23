# Phase 0 + Phase 1 — todo

Plan: docs/plans/phase-0-1-plan.md · Spec: docs/SPEC.md

- [x] Task 1: Repo foundation (scaffold, docker-compose, tooling)
- [x] Task 2: Database schema — migrations + runner
- [x] Task 3: Ingestion layer (network + landing, politeness enforced)
- [x] Task 4: Identity graph engine (normalize/match/merge/resolve + seeds)
- [x] Task 5: Metrics SQL + DQ & Conflict Report
- [x] Task 6: Live pulls, E2E Phase 1 build, CI, README
- [x] Final multi-lens review + fixes (5 lenses, adversarial verification, 11/11 findings fixed)
- [x] BONUS: GCAT orgs.tsv operator enrichment (owner coverage 2.4% → 100% of on-orbit payloads)
- [x] GP element sets landed after 2h politeness window (15,932 rows, cagg + operator views live)
- [ ] USER ACTION: request Space-Track account (creds → .env)

## Review

### Task 6 — live data, E2E build, CI, README (integration)

**Reconciliation (loaders vs. real headers), done before the live pull:**
- GCAT `parse_tsv` was landing the `# Updated <date>` banner line (second line of every GCAT file)
  as a bogus one-column row. Fixed: body lines starting with `#` are skipped. Fixture + test added.
- GCAT numeric coercion (`perigee/apogee/inc`, `norad`) and SATCAT date/numeric coercion made
  defensive: one malformed cell in a 40k–70k-row pull now degrades to NULL (raw kept in `extra` for
  GCAT), instead of aborting the whole landing transaction. Tests added.

**Live pulls (each endpoint once; verified `skipped_fresh` on rerun, zero HTTP):**
- SATCAT bulk: 69,705 rows / 6.6 MB · GCAT satcat: 69,935 / 19 MB · GCAT psatcat: 27,879 / 5.0 MB ·
  GP active: 15,932 / 6.7 MB · SupGP: 0 rows (best-effort, ok) · UCS: graceful skip (no local file).

**Identity build on real data:** 69,878 canonical satellites; 99.75% of GCAT-with-NORAD linked to
SATCAT; 418,086 audited `merge_log` events; 35 status disagreements; status coverage 76.5%.

**Performance fix (integration):** the deterministic matcher's per-row round-trips ran >13 min on
the full catalog. Rewrote the two big passes (SATCAT, GCAT-NORAD) set-based (COPY into a temp table
+ a single `INSERT … RETURNING → merge_log` CTE that preserves the "audit only newly-created links"
idempotency), and pipelined the resolver's per-satellite write loop. Full build now ~45s. Also
deduped by NORAD (`DISTINCT ON`) — live GCAT registers several JCAT pieces under one Satcat number,
which broke a naive `ON CONFLICT DO UPDATE`.

**DQ report fixes (flagship artifact, `docs/reports/dq_report.md`):**
- Ledger table keyed on `(source, endpoint, status)` — all three CelesTrak endpoints share
  `source='celestrak'`, so the old `(source, status)` grouping hid the satcat and gp pulls.
- Status-disagreement section now compares each source's *asserted* status (via `status_mapping`),
  not the resolver's single winning status (which made disagreement structurally invisible → 0).
- Decay-date conflicts compared on *parsed dates* (4,229 real) rather than raw strings (35,277, most
  of them just `1957 Dec  1 1000?` vs `1957-12-01` formatting).

**Deliverables:** `Makefile` `metrics`/`report` targets; `.github/workflows/ci.yml` (ruff + unit,
then a pg17 service-container db job); README rewrite; this file.

**Verification:** `pytest -q -W error` → 111 passed (109 + 2 new). `ruff check .` clean.

### Final review + enrichment wave

- 5-lens review (correctness, spec, data-model, silent-failures, test-quality) with per-finding
  adversarial refutation: 5 confirmed serious + 6 minors, all 11 fixed with covering tests.
  Standouts: half-open SCD2 range join (the spec's own illustrative BETWEEN double-counted the
  transition day — proven live on INTELSAT I); SupGP parser rewritten against the real
  warn-badge page structure (was silently landing 0 rows with status ok).
- Operator enrichment: GCAT orgs.tsv as data-driven operator source (1,435 operators, 4,391
  aliases, 128 subsidiary edges; seed's 17 canonical operators + M&A chains take precedence).
  On-orbit owner coverage 2.4% → 100%; unmatched-owner backlog 1,969 → 8 (JV combined codes).
- GP landed after the 2h window (15,932 OMM element sets); sat_daily cagg refreshed; every daily
  bucket operator-attributed via the identity graph.
- Fixed a real-time-cagg test pitfall: synthetic-data tests must use dates above the
  materialization watermark (dynamic future dates), else rows vanish under rollback isolation.
- Final: 151 tests, -W error, ruff clean. DQ report: 100% operator coverage, 76.8% status
  coverage, 100% of on-orbit payloads with >=2 source identifiers, 35 SATCAT-vs-GCAT status
  disagreements, 4,229 decay-date conflicts.

### Frontend wave (Orbital Economy Terminal)

- FastAPI read-only API (9 endpoints, Postgres-level read_only enforced) + React/Vite/TS SPA,
  built by two parallel agents against a locked DTO contract; F3 integration fixed 2 type drifts,
  14/14 e2e endpoints green on the live DB. SPA deep-link catch-all added.
- Views: Overview (stat tiles, coverage meters, conflicts panel, ingest ledger, congestion
  teaser), Resolver (crosswalk + SCD2 ownership timeline + assertions), Conflicts (3 tabs),
  Operators (league table + detail). Screenshots in docs/img/, wired into README.
- `make fe` → http://localhost:8600. Suite: 169 passed -W error; pnpm build clean.

# Bus Benchmarks (2026-07)

Provenance-tracked performance scoreboard for spacecraft buses by manufacturer and bus model.

- [x] Migration 0009: satellite_bus attribution table + bus_benchmark_snapshots (monthly, immutable)
- [x] Build step: identity/bus.py + scripts/build_bus.py (normalize bus strings, resolve orgs, parent rollup, source_assertion rows, matview refresh, monthly snapshot; wired into daily_ingest.sh)
- [x] Metrics: metrics/bus_benchmarks.sql (mv_bus_behavior_sat + v_bus_sat + per-manufacturer / per-bus benchmark views reusing existing station-keeping / TTO / disposal definitions)
- [x] API: api/routers/buses.py (leaderboard, methodology v1.0, slug detail, per-metric provenance receipts, history)
- [x] Web: 05 BUSES view (leaderboard, detail, receipts, monthly record, methodology panel), mock fixtures
- [x] MCP: mcp_server/ stdio server with bus_benchmarks + bus_detail tools (dependency-free; official SDK does not install on py3.14 toolchain)
- [x] Docs: docs/BUS_BENCHMARKS_METHODOLOGY.md (v1.0, changelog, provenance guarantee, correction channel)
- [x] Tests: router + view cohort tests, build normalization tests, MCP protocol/tool tests (272 total pass with -W error)
- [x] Gates: pytest -W error PASS, ruff clean on changed files, pnpm -C web build PASS; live-db verified (27,849 payloads attributed; 277 manufacturers and 426 bus models at n >= 5)

## Review (Bus Benchmarks)

- Attribution: 27,849 of ~27,933 GCAT payloads got a manufacturer (99.7%), 27,512 a bus model;
  3 org codes unresolved. 12,691 rows rolled up via the curated SPXS -> SPX override, 3,036 via
  GCAT parent chains (business-class parents only, so design bureaus never collapse into
  ministries/agencies).
- Headline (fleet-sorted): SpaceX 12,835 (TTO p50 78 d, SK share 77.3%, SK p50 0.042 km),
  OneWeb 650 (SK share 100%, SK p50 0.005 km), Planet 551 (SK share 0%: passive doves),
  Amazon/Kuiper 398. Disposal-compliance n is ~0 everywhere (v_deorbit_compliance still sparse);
  published as n=0 with no rate rather than a fake 100%.
- Slug fix mid-build: '+' is load-bearing in bus names (BSS-702MP+ vs BSS-702MP), preserved as
  '-plus' in slugs; leading-apostrophe GCAT markers stripped from display names.
