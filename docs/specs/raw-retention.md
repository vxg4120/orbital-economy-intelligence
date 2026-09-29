# Spec: snapshot retention for raw_* (Phase 1) and source_assertion (Phase 2)

**Status:** draft. Phase 1 code is ready, and running it on production is Vib's decision.
Phase 2 is a design question for Vib.
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-09-29

## Goal
Every ingest run lands a full copy of its source in the `raw_*` tables, and
`identity/assertions.py` writes a full set of `source_assertion` rows for every run. Nothing ever
deleted a copy. On 2026-09-28 the box reached 93% disk. After emergency cleanup it was at 88%
with 4.6 GB free, and the growth was about 260 MB a day. The two table families held 10.5 GB,
nearly all of it copies.

- **Phase 1** bounds the `raw_*` tables with a retention policy that none of their readers can
  observe. Every one of those readers selects the newest OK run (churn selects the newest two).
  It frees 5.18 GiB now and stops about 150 MB a day of growth. It also fixes the readers that
  were counting `source_assertion` copies as claims.
- **Phase 2** is `source_assertion` (4.3 GB, about 88 MB a day). Its readers pick the newest
  claim per key, or ask "was this ever claimed", across all runs, so dropping whole runs is not
  invisible there. It needs its own design (see Open questions).

## Architecture decisions
- 2026-09-29: **Policy: per table, keep the newest 3 OK runs plus the first OK run of each UTC
  month, and drop only other runs that finished before the newest OK run.** Rejected:
  latest-N only. Because: a monthly sample of history costs about 130 MB a month for the raw
  tables. Rejected: time-based windows. Because: readers select runs, not dates, and space
  weather lands twice a day.
- 2026-09-29: **Only a finished run can be dropped: status 'ok' or 'error', and older than the
  newest OK run.** A run with no status, an empty status, an unknown status or no ledger row is
  kept whatever its id. Rejected: treating any non-OK run below the newest OK run as failed.
  Because: writers commit their rows before they record 'ok' (`ingest/runlog.py`), so if two
  ingests overlap and the newer finishes first, the older is still in flight below it. That
  would delete the run that churn detection is about to compare against (Codex verify,
  2026-09-29, reproduced with the policy function).
- 2026-09-29: **The policy returns the runs to DROP, so everything not named is kept.**
- 2026-09-29: **The backlog is compacted once by TRUNCATE and reinsert, one transaction per
  table, locking before reading the runs.** Rejected: DELETE then VACUUM FULL. Because:
  deleting 5 GB of rows dirties every heap page, and with `full_page_writes` on that becomes
  gigabytes of WAL on a disk with 4.6 GB free. Rejected: `CREATE TABLE AS` and a rename.
  Because: that breaks the dependent views (`v_fcc_pending_applications`,
  `v_fcc_docket_filing`, `v_space_weather_daily`). TRUNCATE keeps the table's OID, so views,
  grants and identity sequences are untouched. The lock comes first because a row committed
  between the read and the TRUNCATE would otherwise be lost, and a two-connection test races
  exactly that.
- 2026-09-29: **Steady state is a nightly DELETE plus plain VACUUM**, as the last oei step of
  `deploy/nightly-refresh.sh`. Each night drops about one run and lands one, so the freed space
  is reused and the files stop growing without ever needing another exclusive lock.
- 2026-09-29: **`source_assertion` is not pruned by run** (Phase 2). Codex verify showed three
  reader families that whole-run deletion changes: the newest claim per key reverts to an older
  value when the claim's last runs are dropped, "ever claimed" checks
  (`scripts/backfill_gp_history.py:103`) lose superseded values, and one-off sources would age
  out.
- 2026-09-29: **Readers count claims, not copies.** Counting readers select from
  `identity.assertions.CURRENT_ASSERTIONS`: a snapshot feed's (satcat, gcat, ucs) newest run,
  plus every row of any other source, since a one-off `operator_confirmed` claim is never
  re-asserted. The gold-queue evidence takes the newest claim per (attribute, source) with the
  satellite page's tie-breakers verbatim.

## Constraints
- **Deleting production rows needs Vib's explicit approval.** That includes the one-time
  `--compact` and the first deploy that turns on the nightly `--apply` step.
- Never run `--compact` inside the 07:10 or 19:10 UTC nightly windows.
- `KEEP_LATEST` is at least 2, because `identity/churn.py` compares the two newest OK GCAT
  runs. The code refuses anything less.
- Never drop a run that has not finished ('ok' or 'error'), whatever its id.
- Never prune `source_assertion` by run (Phase 2 decides how, if at all).
- No ad-hoc `count(DISTINCT ...)` or other large sort against `source_assertion` on the box.
  On 2026-09-29 one spilled to temp and filled the disk (`temp_file_limit` is unlimited
  there).
- A new `raw_*` table must be added to `SNAPSHOT_TABLES`. The test suite fails until it is.

## Interfaces & dependencies
- `scripts/prune_snapshots.py`: a dry run by default, which prints the plan and changes
  nothing. `--apply` runs the nightly DELETE and VACUUM. `--compact` runs the one-time
  rewrite. `prune_table(conn, table, mode)` does one table inside the caller's transaction,
  with `lock_timeout = 10s`.
- `deploy/nightly-refresh.sh`: `prune_snapshots.py --apply` runs as the last oei step, after
  every reader of tonight's runs.
- Deploy order matters. `deploy/` is bind-mounted, so the nightly line goes live on
  `git pull`. `scripts/` is baked into the image, so the image must be rebuilt first, or the
  step soft-fails with "!! oei prune_snapshots failed" until it is.
- `identity/assertions.py` exports `CURRENT_ASSERTIONS`, a table expression that
  `quality/report.py`, `quality/audit_report.py` and `scripts/build_graph.py` select from.

## Edge cases
- **Keys that vanish from a feed.** Two per-object readers take a key's newest raw row across
  runs rather than from the newest run (`scripts/build_gold_queue.py` for perigee/apogee and
  RCS). Once a key's last runs are dropped, they fall back to its newest kept run, or to
  nothing. SATCAT keeps decayed objects, so this touches only keys GCAT renames. The gold
  queue is a manual labelling tool.
- **A check that lands zero rows** (SupGP with no anomalies) counts as a check. The report
  scopes on the ledger, so a clean check reads as zero, not as the previous check's list.
- **Identity and generated columns.** `raw_supgp_status`'s identity is reinserted with
  `OVERRIDING SYSTEM VALUE`, so kept rows keep their ids and the sequence is not reset.
  Generated columns are skipped and recomputed.
- **TRUNCATE is not MVCC-safe.** A transaction whose snapshot predates the compaction's commit
  sees the table empty. The API uses READ COMMITTED, where a statement blocked on the lock
  takes its snapshot after the lock is granted, so the exposure is a transaction already open
  across the commit. That's one more reason to compact at a quiet hour, outside the nightly.
- **A run's rows can span a partial failure.** Each table commits on its own, so an interrupted
  `--compact` leaves some tables compacted and the rest untouched. Both states are consistent,
  and re-running finishes the job.
- **Monthly keepers are a sample, not an archive of published inputs.** The first OK run of a
  month is not guaranteed to be the run a monthly snapshot was built from. Exact traceability
  would need the snapshot to record its run ids.

## Acceptance criteria
- [x] `pytest -q -W error tests/test_prune_snapshots.py` passes against a migrated database:
  12 tests, 3 of them database-backed, including the two-connection race. Mutating the code to
  read before locking, or to drop unfinished runs, fails the suite.
- [x] Against the same clean database, the full suite's failures on this branch are identical
  to `origin/release/audit-20260917`'s, with more passes. The recorded run is below in the
  decision log.
- [x] Rehearsal at production's row count: a local `raw_gcat_satcat` with 3.43M rows in 49
  nightly runs goes from 1,127 MB to 116 MB under `--compact` in 3.7 s end to end. It keeps
  July's first run, August's first and the newest three, and a following `--apply` drops
  nothing.
- [ ] Production dry run (`docker compose exec -T oei-api python scripts/prune_snapshots.py`)
  plans about 5.2 GB of drops, and every table keeps its newest OK run.
- [ ] After `--compact` on production, `df -h /` shows at least 5 GB more free than before,
  and orbital, exo and the landing all return 200.
- [ ] After three nightlies, `grep prune_snapshots deploy/refresh.log` shows three runs, none
  failed, and each raw table is within one run of its post-compaction size.
- [ ] The next audit report prints roughly 700k assertions (one run per snapshot feed), not
  ~33M.

## Open questions
- (Vib) Approve Phase 1: the policy (the newest 3 plus each month's first) and a window for the
  one-time `--compact`.
- (Vib) **Phase 2: how should `source_assertion` stop growing?** Measured 2026-09-29: 32.8M
  rows, 4.3 GB, about 88 MB a day. After Phase 1 the disk has roughly 3.5 months of runway
  for it. Options:
  1. *Run-level retention, protecting each key's newest row.* It keeps every claim the site
     shows, byte for byte. The three "ever claimed" readers (`backfill_gp_history.py`,
     `build_gold_queue.py`'s rideshare selector, the audit report's two-source denominator)
     change to "claimed within the kept window", which is almost certainly what they meant.
     It needs a per-key aggregate over the table at each prune.
  2. *Drop only exact copies.* Delete a row only when the same claim is re-asserted in the
     feed's next run. That's lossless for every reader, but the one-time job is a heavy
     per-run-pair pass (an index on `(source, ingest_run_id)` first).
  3. *Stop writing copies (recommended long-term).* Record a claim once and close it when it
     changes (valid-from / valid-to runs), as `satellite_identifier` already does. That's the
     root fix, but it touches every reader.
- (Claude) `satellite_status_history` gains a row per satellite per run even when nothing
  changed: 259 MB and 2.2M rows today, and the Resolver timeline lists about 49
  near-identical entries per object. It belongs with Phase 2 option 3.
- (Claude) The reader audit flagged that `identity/assertions.py` joins `satellite_identifier`
  without `valid_to IS NULL`. If so, links retired by churn keep receiving fresh claims. That
  is unverified and an identity-semantics change, so it is out of this branch.
- (Vib) The CI `db` job runs on an empty database, where 91 data-dependent tests fail on the
  base branch too (for example `tests/test_api_buses.py` expects published buses). Seed a
  fixture, or mark those tests as needing a populated graph.

## Decision log & lessons learned
- 2026-09-29 (Claude): the growth went unnoticed because every raw reader was correctly
  latest-run scoped, so nothing ever read the history. Lesson: an append-per-run table needs
  its retention designed with the table.
- 2026-09-29 (Claude): a reader audit (subagent) found five readers counting copies as claims,
  including an audit-report total that would have printed ~32.8M assertions (49x), and
  gold-queue evidence that showed reviewers the oldest claim. No test had seeded more than one
  run, so each new test seeds two.
- 2026-09-29 (Codex verify, confirmed by Claude): the first draft (1) dropped non-OK runs below
  the newest OK run, which deletes an older ingest still in flight; (2) pruned
  `source_assertion` by run, which reverts newest-per-key claims and erases "ever claimed"
  evidence; (3) scoped counts to the newest run for every source, hiding one-off claims; and
  (4) lacked the API's `source_key` tie-breaker in gold evidence. Each was reproduced against
  the code before being fixed, and each fix has a test that fails without it.
- 2026-09-29 (Claude): a 3% sample of `source_assertion` on production (2,100 satellites,
  19,634 claim keys) found no claim whose newest row predates its feed's latest run. That is
  sample evidence that option 1 of Phase 2 would change nothing visible today, not a
  guarantee.
- 2026-09-29 (Claude): the full suite ran on a clean `timescale/timescaledb:latest-pg17` (as
  CI does) for both the base and this branch. The failure sets were identical (91 failed and 5
  errors, all data-dependent tests on an empty database), and the branch passed 390 tests
  against the base's 372.
