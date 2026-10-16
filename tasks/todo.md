# Phase 0 + Phase 1 — todo

## 2026-09-17 — User-confirmed first QA year

- [x] Verify deployed graphic and release branch incorrectly say2015.
- [x] Restore first-QA constellation milestone to2014, confirmed directly by Vib.
- [x] Preserve separately listed employer dates; inspect the exact one-line HTML diff.
- [x] Operator published corrected landing page; public GET independently verified first-QA milestone2014 on2026-09-17.

Review: minimal factual correction on isolated branch from release/audit-20260917;
no runtime behavior changed, no production action performed.

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

## 2026-09-17 — Close remaining site audit defects

Spec: `docs/specs/audit-followup-20260917.md`.

- [x] Recheck peer/source state and isolate from released ae3454e.
- [x] Prove local404 routing; correct config mount and add checked operator smoke.
- [x] Implement valid Operators Active sort and shared conflict warm caches.
- [x] Run hermetic focused tests and desktop/375px browser verification.
- [x] Commit reviewed source, run independent Codex verification and resolve findings.
- [x] Record deployment/configuration prerequisites separately from source completion.

Review: source fixes verified; independent review found one test-validity issue,
reproduced and resolved in9e0edfd.14 focused SQL/API tests and3 Caddy tests passed;
desktop/mobile browser evidence passed. See tasks/independent-followup-review.md.
Live404 was repaired independently by Claude and confirmed by public GET; directory
mount adoption and API release remain operator-owned. No production actions by Codex.

## 2026-10-12 — Retire the per-run assertion ledger (assertion-model.md, criterion 7)

Condition met: nine green nightlies on the claim table (2026-10-07 19:10 to 2026-10-11 19:10).

- [x] Migration 0024: rename `source_assertion` to `source_assertion_runs_20261012`, drop its
      FK to satellite, drop `claim_is_current`.
- [x] One writer: `identity/claims.py` owns the attribute lists and `_latest_run`; the
      bootstrap gate goes (no ledger to replay); `identity/assertions.py`, `build_claims.py`,
      `compare_claims.py` deleted; `build_graph.py` and `build_bus.py` stop calling `extract`;
      `merge.py` stops repointing the ledger.
- [x] Tests moved to the claim table (claims, sibling links, merge, reconcile, pipeline,
      migrations, bus build, conflicts fixture); full suite green on the local DB after 0024.
- [x] Codex verify against the spec; findings resolved.
- [x] Deploy outside the windows: pull, rebuild oei-api, migrate; verify views, resolver
      timing (criterion 6), site; spec criteria 5-7 and decision log; memory; peers told.
- [ ] After 2026-10-19: `DROP TABLE source_assertion_runs_20261012`, VACUUM, df.

Review: deployed 2026-10-12 00:36 UTC (main a8f783d), migration applied on production in the
same minute; v_current_claim 704,324 rows, progress unchanged, sites and the conflicts, satellite
and buses APIs 200. Codex found two things, both taken: the renamed table is not a rollback by
itself (procedure written and rehearsed) and the missing gate could strand un-replayed history
(migration preflight, both branches exercised). Criterion 5 closes 2026-10-14; the DROP after
2026-10-19.


# Improvement batch, 2026-10-16 (Vib: "Do all of that", "Also work on improving design")

Owner: session space-7e. Peers: space-28 owns deploy/landing/* copy tonight (branch
landing/oct-2026-copy); space-a2 owns the source_assertion_runs_20261012 drop after 10-19.

## Needs nothing from Vib
- [x] 1. orbital `/api/health` route (parity with exo's), covered by a test, deployed 10-16 02:57 UTC (c6c7b2d).
- [x] 2. Latency: `/api/buses` 4.2 s -> 1.4 s (SQL 3.3 s -> 0.19 s, migration 0025
      mv_latest_satellite_status); exo landing cold 2.4/2.8 s -> 0.66/0.63 s (warm caches,
      exodossier a86c356). Both measured on the box after deploy.
- [x] 3. CI: tried seeding the fixtures before pytest; 5 of 96 graph tests pass, the rest
      are by design about the real catalog. Not viable; the skip-on-empty rule stays. The
      experiment found and fixed the audit_report crash on an empty Eutelsat cohort.
- [x] 4. Restore rehearsal passed 10-16 02:41-02:50 UTC (procedure in deploy/README.md).
- [x] 5. Phone/accessibility pass shipped on both apps 10-16 03:13 and 03:19 UTC (space 8436531,
      exodossier e8341d9): the stat strip, nav and tables get scroll cues, 10px text gone on
      phones, --ink-ghost text to --ink-faint, 36px+ touch targets, skeleton loading, exact
      figures on compact numbers, h2 panel titles. Open: whether exo adopts orbital's serif
      heading layer (P2-4), Vib's call.

## Needs a decision from Vib (present, don't build)
- [ ] 6. Space-Track: creds are wired through nightly-refresh.sh and the client, but only
      scripts/backfill_gp_history.py uses the client. Confirm what the nightly gains, then
      Vib pastes creds into /root/apps/space/deploy/.env himself.
- [ ] 7. Exo light curves: nothing live reads the pipeline; the Docker image omits
      pipeline/. Decision is whether to build Wave 2 (recovery results on the site).
- [ ] 8. GCAT: main fix shipped 10-07. Two open decisions: bus.py rule 2 for NORAD-less
      rows; whether status_map.yml keeps GCAT code C -> DECAYED (23 objects).
- [x] 9. 31% vs 31-37% copy: handed to space-28; source is splitstep build-journal.md:95-96.

## Review (2026-10-16 03:20 UTC)
Shipped and measured on the box: /api/health (orbital); /api/buses 4.2 s -> 1.4 s; exo landing
cold 2.4+2.8 s -> 0.66+0.63 s; restore rehearsal passed and documented; phone pass on both
apps (audit and after-metrics in the session scratchpad, report tables in the merge commits'
branches). Not done, by finding: CI graph tests cannot run on fixtures (5/96 pass), so the
skip-on-empty rule stays. Reviews: Codex was down for the account all night ("model is not
supported when using Codex with a ChatGPT account", needs `codex login`); each diff got an
independent Claude review instead, which caught two CI-breaking tests and an unbounded
cache staleness, both fixed before deploy. Lesson recorded: never Write over tasks/todo.md.

# Decisions batch, 2026-10-16 07:12 UTC (Vib: "Yes to all" to items 6-8 and P2-4)
Owner: session space-7e. One implementer per item in its own worktree/branch, an independent
review each, then merge and deploy. space-28 owns landing/stories-20261016 in both repos
(landing route + its own read-only endpoints) at the same time; we keep off those paths.
- [x] A. (merged f3cbe6f, deployed 08:15 UTC) Exo serif headings (P2-4): port orbital's Fraunces heading/display layer to exo.
      Branch exodossier design/serif-headings-20261016.
- [x] B. (merged a8a1357, deployed 07:53 UTC, migration 0027; awaits creds) Space-Track: periodic gp_history top-up + decay-message landing, as a nightly step
      that no-ops without SPACETRACK_IDENTITY/PASSWORD. Branch space feat/spacetrack-topup.
      Vib creates the account and pastes creds into /root/apps/space/deploy/.env himself.
- [x] C. (merged 882605c, deployed 08:15 UTC with migration 0007; found: the nightly graph build truncated the result tables via FK cascade, so results are now keyed on TOI; production run from the laptop in progress) Exo light curves Wave 2: a page showing transit-recovery results from the pipeline.
      Branch exodossier feat/lightcurves-wave2.
- [x] D. (merged 0d9a384, deployed 07:53 UTC, migration 0026; 0 moves, 557 relabels) GCAT: bus.py rule 2 for NORAD-less rows follows the key's own number (methodology
      1.9 -> 1.10, changelog); GCAT code C -> DECAYED stays, documented as decided.
      Branch space feat/gcat-rule2.
- [x] E. (space-28) Landing data stories on both apps, reviewed by space-7e, deployed with Vib's
      "Deploy the landing stories": orbital b9bccdf live 08:10 UTC, exo 2fed917 live 08:16 UTC.
