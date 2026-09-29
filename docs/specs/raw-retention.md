# Spec: snapshot retention for raw_* and source_assertion

**Status:** draft (code ready; running it on production is Vib's decision)
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-09-29

## Goal
Every ingest run lands a full copy of its source in the `raw_*` tables, and
`identity/assertions.py` writes a full set of `source_assertion` rows per run. Nothing ever
deleted a copy, while every reader wants only the newest OK run (`identity/churn.py` wants the
newest two). On 2026-09-28 the box reached 93% disk. After emergency cleanup it was at 88%
with 4.6 GB free, and the copies were 8.8 of the 10.5 GB these tables held, growing about
260 MB a day. This workstream bounds the tables with a retention policy that no reader can
observe, returns the backlog to the operating system once, and fixes the readers that were
counting copies as claims.

## Architecture decisions
- 2026-09-29: **Policy: per stream, keep the newest 3 OK runs plus the first OK run of each
  UTC month, and drop the rest.** Rejected: latest-N only, which frees 9.7 GB instead of 8.8.
  Because: a month's published numbers (bus benchmarks, reports) stay traceable to the inputs
  that produced them, for about 300 MB a month. Rejected: time-based windows. Because: readers
  select runs, not dates, and two runs a day (space weather) would make a day-based window
  keep a variable number.
- 2026-09-29: **The policy returns the runs to DROP, so everything not named is kept.** Runs
  newer than the newest OK run (an ingest in flight), and every run of a stream with no OK
  run, are never dropped. Rejected: computing a keep set and deleting its complement. Because:
  with a keep set, every unforeseen case deletes.
- 2026-09-29: **A stream is one feed in one table.** A `raw_*` table is one stream.
  `source_assertion` splits on its own `source` column, and only `satcat`, `gcat` and `ucs`
  are pruned. Rejected: splitting on `ingest_run.source`. Because: SATCAT runs are logged as
  `celestrak`, a label that GP, space weather and SupGP share, and `gcat` spans three
  endpoints (reader audit, 2026-09-29).
- 2026-09-29: **The backlog is compacted once by TRUNCATE and reinsert, one transaction per
  table.** Rejected: DELETE then VACUUM FULL. Because: deleting 8.8 GB of rows dirties every
  heap page, and with `full_page_writes` on, that means gigabytes of WAL on a disk with
  4.6 GB free. Rejected: `CREATE TABLE AS` and a rename. Because: it breaks the dependent
  views (`v_fcc_pending_applications`, `v_fcc_docket_filing`, `v_space_weather_daily`,
  `v_killer_chart`). Rejected: pg_repack, which is not installed. TRUNCATE keeps the table's
  OID, so the views, grants and identity sequences are untouched.
- 2026-09-29: **Steady state is a nightly DELETE plus plain VACUUM**, as the last oei step of
  `deploy/nightly-refresh.sh`. Each night drops about one run and adds one, so the freed space
  is reused and the files stop growing without ever needing another exclusive lock.
- 2026-09-29: **Readers count claims, not copies.** Counting readers join on
  `identity.assertions.LATEST_RUN_PER_SOURCE`, and the gold-queue evidence takes the newest
  claim per (attribute, source), as `api/routers/satellites.py` already does. These readers
  were wrong before retention and would have shifted silently during it.

## Constraints
- **Deleting production rows needs Vib's explicit approval.** That includes the one-time
  `--compact` and the first deploy that turns on the nightly `--apply` step.
- Never run `--compact` inside the 07:10 or 19:10 UTC nightly windows. It locks each table
  while that table is rewritten.
- `KEEP_LATEST` is at least 2, because `identity/churn.py` compares the two newest OK GCAT
  runs. The code refuses anything less.
- Never split streams on `ingest_run.source` (see the decisions above).
- Never prune a `source_assertion` source other than satcat, gcat or ucs. Nothing re-asserts
  the others, so ageing one out loses it for good.
- No ad-hoc `count(DISTINCT ...)` or other large sort against `source_assertion` on the box.
  On 2026-09-29 one spilled to temp and filled the disk (`temp_file_limit` is unlimited
  there).
- A new `raw_*` table must be added to `SNAPSHOT_TABLES`. The test suite fails until it is.

## Interfaces & dependencies
- `scripts/prune_snapshots.py`: a dry run by default, which prints the plan and changes
  nothing. `--apply` runs the nightly DELETE and VACUUM. `--compact` runs the one-time
  rewrite. Each table gets its own transaction with `lock_timeout = 10s`.
- `deploy/nightly-refresh.sh`: `prune_snapshots.py --apply` runs as the last oei step, after
  every reader of tonight's runs.
- Deploy order matters. `deploy/` is bind-mounted, so the nightly line goes live on
  `git pull`. `scripts/` is baked into the image, so the image must be rebuilt first, or the
  step soft-fails with "!! oei prune_snapshots failed" until it is.
- `identity/assertions.py` exports `LATEST_RUN_PER_SOURCE`. `quality/report.py`,
  `quality/audit_report.py` and `scripts/build_graph.py` join on it.

## Edge cases
- **An ingest in flight** has rows but no OK status yet, and it is never dropped.
- **Failed runs** older than the newest OK run are dropped, since no reader selects them.
- **A ledger row missing** for a run in a table means the run's stream has no OK run, so the
  stream is kept whole.
- **A check that lands zero rows** (SupGP with no anomalies) still counts. The report scopes
  on the ledger, so a clean check reads as zero rather than as the previous check's list.
- **Claims a source stopped making.** These are keys whose newest row is older than the
  source's latest run, and a newest-per-key reader shows their frozen last value. They
  survive only while a kept run holds them. Measured on 2026-09-29: **0** of 19,634 claim
  keys in a 3% sample (2,100 satellites across old, middle and new ids), so the policy has no
  visible effect today.
- **Identity columns** (`source_assertion.assertion_id`, `raw_supgp_status`'s id) are
  reinserted with `OVERRIDING SYSTEM VALUE`, so kept rows keep their ids and the sequence is
  not reset. Generated columns are skipped.
- **Monthly keepers accumulate** about 300 MB a month (one full run of every stream). That's
  a policy knob, not a leak.

## Acceptance criteria
- [x] `pytest -q -W error tests/test_prune_snapshots.py` passes against a migrated database,
  3 of its tests database-backed. Verified 2026-09-29: 15 passed.
- [x] Against the same clean database, the full suite's failures on this branch are identical
  to `origin/release/audit-20260917`'s, with more passes. Verified 2026-09-29: 91 failed and
  5 errors on both (data-dependent tests on an empty DB), 389 passed against 372.
- [x] At 1/8 of production's `source_assertion` (4.2M rows, 96 runs), `--compact` keeps
  exactly July's first run, August's first and the newest three per feed, runs in 12 s, and a
  second `--compact` reports nothing to drop.
- [ ] Production dry run (`docker compose exec -T oei-api python scripts/prune_snapshots.py`)
  plans about 8.8 GB of drops, with no stream losing its newest OK run.
- [ ] After `--compact` on production, `df -h /` shows at least 8 GB more free than before,
  and orbital, exo and the landing all return 200.
- [ ] After three nightlies, `grep prune_snapshots deploy/refresh.log` shows three runs, none
  failed, and each table's size is within one run of its post-compaction size.
- [ ] The next audit report prints roughly 700k assertions (one run per source), not ~33M.

## Open questions
- (Vib) Approve the policy (3 newest plus each month's first) and a window for the one-time
  `--compact`.
- (Vib) Keep the monthly snapshots? Dropping them frees another 0.84 GB now and about 300 MB a
  month.
- (Claude) `satellite_status_history` gains a row per satellite per run even when nothing
  changed. It's 259 MB and 2.2M rows today, and the Resolver timeline lists about 49
  near-identical entries per object. It needs a run-length design, not this policy.
- (Claude) The reader audit flagged that `identity/assertions.py` joins
  `satellite_identifier` without `valid_to IS NULL`. If so, links retired by churn keep
  receiving fresh claims. This is unverified and is an identity-semantics change, so it stays
  out of this branch.
- (Vib) The CI `db` job runs on an empty database, where 91 data-dependent tests fail on the
  base branch too. Either seed a fixture or mark those tests as needing a populated graph.

## Decision log & lessons learned
- 2026-09-29 (Claude): the growth was invisible because every reader was correctly
  latest-run scoped. Nothing ever read the history, so nothing ever noticed it. Lesson: an
  append-per-run table needs its retention designed with the table, not after the disk fills.
- 2026-09-29 (Claude): a reader audit (subagent) found five readers counting copies as
  claims, including an audit-report total that would have printed ~32.8M assertions (49x),
  and gold-queue evidence that showed reviewers the oldest claim. No test had seeded more
  than one run, so each new test here seeds two.
