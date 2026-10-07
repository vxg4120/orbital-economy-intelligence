# Spec: claims, not copies (source_assertion Phase 2)

**Status:** active. Vib: "go for it, do what you think is best quality" (2026-10-06); option 3 chosen.
Step 1 (table, writer, replay, nightly dual-write) built 2026-10-07; readers not yet moved.
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-10-06

## Goal
`source_assertion` holds one row per (source, key, attribute) per ingest run: every run
re-asserts a feed's full set of claims, and nothing deletes a copy. On 2026-10-05 it was 35M
rows and 4.7 GB, growing about 64 MB a day, and the copies cost more than disk: the resolver was
pulling 33M of them into Python each night until 2026-10-05 (the 13-to-90-minute nightly), and
every counting reader had to be taught to count claims rather than copies. The goal is a table
whose size is the number of claims, where "what does each source currently say" and "what did
it say on a date" are both one cheap query, with no reader able to tell the difference.

## Architecture decisions
- 2026-10-06 — **Chosen: store each claim once with a validity range (option 3).** A row is
  (source, source_key, attribute, value, first_run, observed_from, closed_run, observed_to);
  the writer opens a row when a (source, key, attribute, value) first appears and closes it at
  the first run that no longer makes it. Current claims are rows with `closed_run` NULL;
  history is a range query.
- 2026-10-07 — **Claims are about source keys, not satellites.** Which satellite a claim is
  about is the crosswalk's business, joined at read time (`v_current_claim`), so retiring a
  link removes a sibling's claims on the spot, identity merges never touch claims, and an
  unmatched object is an open claim whose key identifies no satellite. Rejected: a
  satellite_id column repointed by merges, as source_assertion has. Because: that is how the
  double-linked GCAT keys put one object's claims on two satellites.
- 2026-10-07 (after Codex verify) — **Runs are recorded strictly in order, once each, one
  writer per feed at a time.** `claim_progress` holds each feed's last recorded run and its
  observation time, advanced on every recorded run, changes or not; a run at or before it is
  refused; a transaction-scoped advisory lock on the feed serializes the nightly and the
  replay. Rejected: deriving the watermark from the claims themselves. Because: an unchanged
  run would leave it behind and a late run could slip in and contradict a processed one.
- 2026-10-07 (after Codex verify) — **Bootstrap is the replay's job.** The nightly records a
  feed only once its progress row exists, and the replay holds the feed's lock for its whole
  pass, so a nightly cannot record its newer run first and leave the history refused.
- 2026-10-07 (after Codex verify) — **An empty snapshot is not evidence that every claim
  ended**; it is refused. Completeness of a snapshot is the ingest ledger's business.
- 2026-10-07 (after Codex verify) — **`observed_to` is the last observation, not the first
  absence**: closing at run R stamps the previous recorded run's time; `closed_run` is R. For
  open claims the last observation is the feed's progress time, which `v_current_claim`
  exposes as `observed_to`, so the Resolver's "last observed" survives.
- 2026-10-07 (after Codex verify) — **Only the three snapshot feeds** (satcat, gcat, ucs) are
  recorded or replayed. A correction channel is additive, not a snapshot; it gets its own
  path when it exists. Rejected: run-level
  retention like raw_* (option 1), because newest-per-key readers revert to older values and
  "ever claimed" readers lose superseded values (Codex verify, 2026-09-29), and because it
  leaves the copies in place for the kept runs. Rejected: deleting only exact copies of the
  next run's claim (option 2), because it is lossless but leaves the schema that caused the
  problem, and the one-time pass needs a new index and a per-run-pair walk over 35M rows.
- 2026-10-06 — **Writes stay idempotent per run.** Re-running extraction for the same run
  must not open or close anything (the current writer's contract, identity/assertions.py).
- 2026-10-06 — **Readers migrate to one view, `v_current_assertion`**, before the writer
  changes, so the cutover is a view swap. The reader inventory (2026-09-29 audit) is the
  checklist: api/routers/conflicts.py, api/routers/satellites.py, metrics/benchmark_views.sql
  (`v_killer_chart`), quality/report.py, quality/audit_report.py, identity/resolve.py,
  scripts/build_gold_queue.py, scripts/backfill_gp_history.py, scripts/build_graph.py.

## Constraints
- No deletion from `source_assertion` on production before the new table is built, verified
  against it, and backed up (the raw_* precedent: a laptop dump first).
- The history replay runs under nohup, outside the nightly windows, with `temp_file_limit`
  set (the script sets 2 GB), after the laptop backup; it holds each feed's advisory lock for
  its pass, so a nightly that fires meanwhile waits on the claims step.
- The cutover is reversible for at least one week: the old table is renamed, not dropped.
- Never a large sort on production `source_assertion` outside a planned window with
  `temp_file_limit` set; the one-time build streams per source and attribute.
- `observed_at` semantics the Resolver shows as "last observed" must survive as `observed_to`.
- Every reader in the inventory has a before/after comparison on production data (counts,
  conflicts, resolved values) that is byte-identical, except where the spec names the change.

## Interfaces & dependencies
- Writer: identity/assertions.py `extract()`; its callers in scripts/build_graph.py.
- `identity.assertions.CURRENT_ASSERTIONS` becomes `SELECT * FROM v_current_assertion`.
- The one-time build is a script (scripts/build_claims.py) with `--dry-run`, run under nohup
  on the box like the raw_* compaction, outside the 07:10 and 19:10 UTC windows.
- Phase 1's prune (scripts/prune_snapshots.py) is unaffected; raw_* stay as they are.

## Edge cases
- A key linked to two satellites (see docs/specs/gcat-sibling-links.md) produces two rows per
  claim today; the new model keeps one row per (satellite, source, key, attribute, value) and
  so inherits the duplication until the links are fixed. Fix the links first.
- A value that flips A -> B -> A produces three rows, not two; "ever claimed A" stays true.
- A feed that stops asserting a key closes its rows (observed_to = that run); newest-per-key
  readers must decide whether a closed claim is shown. Today they show it; the view keeps that
  behaviour by default (`v_current_assertion` = open rows plus the newest closed row per key
  when nothing is open), and the conflicts page gets an explicit "stale claim" flag.
- Identity merges repoint satellite_id on all rows (identity/merge.py); ranges stay valid.
- satellite_status_history has the same shape (one row per satellite per run, 2.2M rows) and
  the Resolver timeline shows ~49 identical entries per object; it moves to the same model in
  the same change.

## Acceptance criteria
- [x] Writer and replay (tests/test_claims.py, 8 tests): open/close/unchanged, same-or-older
  run refused with progress advancing on unchanged runs, empty snapshot refused, the bootstrap
  gate, A->B->A as three rows, row-for-row agreement with the assertion writer on a seeded
  snapshot, the view through current links only, and a replayed history equal to one recorded
  live. Mutants of each guard fail the suite. Full suite 420 passed.
- [ ] After the production replay, `v_current_claim` equals the current readers' output
  (`claim_is_current` over `CURRENT_ASSERTIONS`) row for row (`EXCEPT` both ways returns
  nothing), and `claim_progress.last_run` per feed equals the feed's newest run.
- [ ] The new table built from production has one row per claim: about 1.4M rows against 35M
  (measured 2026-10-05: 140,881 current (satellite, source) pairs per attribute).
- [ ] For every reader in the inventory, the before/after comparison script reports identical
  output, or a difference the spec names.
- [ ] A nightly on the new model adds rows only for changed or new claims; the table's growth
  over a week is under 5 MB.
- [ ] The resolver's `_assertions` runs in under 2 s per attribute on the new table (13 s on
  the old one).
- [ ] The old table is renamed `source_assertion_runs_<date>` and dropped only after seven
  green nightlies.

## Open questions
- (Vib) Approve option 3, or prefer the lighter option 2 as a stopgap.
- (Vib) Closed claims on the conflicts page: shown with a "no longer asserted" flag, or hidden?
- (Claude) Whether the view can serve "as of run N" cheaply enough for the audit report's
  monthly denominators, or those keep a monthly snapshot table.

## Decision log & lessons learned
- 2026-10-06 (Claude) — Spec drafted from the 2026-09-29 reader audit, the retention work, and
  the resolver fix. The per-run copies were the root cause of three separate incidents: the disk
  at 93%, counts 49x too high in reports, and the 90-minute nightly.
