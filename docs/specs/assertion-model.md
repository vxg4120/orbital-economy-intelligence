# Spec: claims, not copies (source_assertion Phase 2)

**Status:** draft, for Vib's decision on the architecture choice below
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
- 2026-10-06 — **Recommended: store each claim once with a validity range (option 3).** A row
  is (satellite_id, source, source_key, attribute, value, first_run, last_run, observed_from,
  observed_to); the writer opens a row when a (source, key, attribute, value) first appears and
  closes it when the value changes or the key stops being asserted. Current claims are rows
  with `last_run` = the feed's newest run; history is a range query. Rejected: run-level
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
- [ ] `v_current_assertion` on the old table equals `CURRENT_ASSERTIONS` row for row on a
  production snapshot (`EXCEPT` both ways returns nothing).
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
