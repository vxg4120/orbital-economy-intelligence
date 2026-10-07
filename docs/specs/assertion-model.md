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
- A feed that stops asserting a key, or an attribute of it, closes the claim (observed_to =
  the last run that made it). A closed claim is not current, even through a current key:
  `v_current_claim` is "what does each source say now", the one definition every reader
  uses (decided 2026-10-07, see the log). "Ever claimed" readers (the GP backfill's
  ever-a-payload cohort, the gold queue's never-had-a-SATCAT-claim stratum) read `v_claim`,
  open and closed claims through current links.
- Identity merges do not touch claims: the crosswalk link moves, and the claim follows it
  through the view (identity/merge.py still repoints source_assertion until that table goes).
- satellite_status_history has the same shape (one row per satellite per run, 2.2M rows) and
  the Resolver timeline shows ~49 identical entries per object; it moves to the same model in
  a follow-up change, not this one (out of scope 2026-10-07: the claim replay is the risky
  step and goes alone).

## Acceptance criteria
- [x] Writer and replay (tests/test_claims.py, 8 tests): open/close/unchanged, same-or-older
  run refused with progress advancing on unchanged runs, empty snapshot refused, the bootstrap
  gate, A->B->A as three rows, row-for-row agreement with the assertion writer on a seeded
  snapshot, the view through current links only, and a replayed history equal to one recorded
  live. Mutants of each guard fail the suite. Full suite 420 passed.
- [x] After the production replay, `v_current_claim` equals the current readers' output
  (`claim_is_current` over `CURRENT_ASSERTIONS`) row for row (`EXCEPT` both ways returns
  nothing), and `claim_progress.last_run` per feed equals the feed's newest run. Measured
  2026-10-07 05:05 UTC: 704,323 rows each way, both EXCEPTs 0; satcat at run 4557, gcat at
  4558; UCS has no history on production (never ingested), so no progress row.
- [x] The new table built from production has one row per claim: 728,766 claims (703,661
  open) against 37.0M ledger rows, 161 MB against 4.7 GB. (The spec's 1.4M estimate counted
  (satellite, source) pairs per attribute across five attributes; a claim is per attribute,
  and GCAT and SATCAT make about 350k each.) The replay took 10 minutes: the index build about
  3, then 106 runs at 1.2 to 2 s each.
- [x] For every reader in the inventory, the before/after comparison script
  (scripts/compare_claims.py: ledger vs view both ways, resolver winners per attribute,
  progress per feed) reports identical output, or a difference the spec names: the resolver's
  "no longer claimed" count per attribute is that difference, recorded in the log with its
  production numbers: 2026-10-07, name/object_type/owner 140,881 winners each, 0 changed, 0
  withdrawn; status 124,357 same, 1 withdrawn; decay_date 72,189 same, 4 withdrawn (STARLINK-
  38308's SATCAT decay 2026-09-07 dropped by run 4283, Electron Stage 2's GCAT "2025 Apr 4
  0620?" dropped by run 3263, Briz-M 88520, Zhixing 2A, and OBJECT B's SATCAT status "+").
  The readers themselves, 2026-10-07 05:15 UTC: quality/report.py, quality/audit_report.py,
  the conflicts counts and v_killer_chart run read-only from both trees against production.
  The dq report and the counts are byte-identical (bar timestamps and the unmatched heading);
  the audit report's data basis counts claims (703,661; gcat 401,000) where it counted claims
  times links (704,323; 401,662: GCAT keys still linked to two satellites until the first
  retirement nightly), and decay-date coverage is 35,601 against 35,602 (one withdrawn date).
- [ ] A nightly on the new model adds rows only for changed or new claims; the table's growth
  over a week is under 5 MB.
- [ ] The resolver's `_assertions` runs in under 2 s per attribute on the new table (13 s on
  the old one).
- [ ] The old table is renamed `source_assertion_runs_<date>` and dropped only after seven
  green nightlies.

## Open questions
- (Vib) Approve option 3, or prefer the lighter option 2 as a stopgap.
- (Vib, answered by Claude 2026-10-07, revisit if wanted) Closed claims on the conflicts page:
  hidden. A conflict is between what sources say now; a claim a feed withdrew is history,
  and the API, conflicts page and quality report already read only each feed's newest run.
- (Claude) Whether the view can serve "as of run N" cheaply enough for the audit report's
  monthly denominators, or those keep a monthly snapshot table.

## Decision log & lessons learned
- 2026-10-06 (Claude) — Spec drafted from the 2026-09-29 reader audit, the retention work, and
  the resolver fix. The per-run copies were the root cause of three separate incidents: the disk
  at 93%, counts 49x too high in reports, and the 90-minute nightly.
- 2026-10-07 (Claude, after the Codex verify of the readers branch) — **One definition of
  current, everywhere.** Codex found that the resolver, the gold evidence, the audit report's
  accountability and integrity sections and v_killer_chart had read the newest copy over every
  retained run, so a value a feed stopped making lived on until its run was pruned, while the
  API, conflicts page and quality report read only each feed's newest run. The spec had
  promised a newest-closed fallback view; rejected in favour of the stricter rule, because two
  definitions of "current" were how the reports and the site disagreed in the first place, and
  a withdrawn decay date resolving as if still asserted is the wrong answer (the resolver now
  retracts it). The difference is measured on production by scripts/compare_claims.py before
  the readers move, and recorded here. Readers that mean "ever claimed" use `v_claim`.
- 2026-10-07 (Claude, after Codex) — External field names survive the model: the satellite
  detail API keeps `observed_at` on its assertion rows (the Resolver page reads it), sourced
  from the claim's `observed_to`. A test pins it. Lesson: a renamed column in a dict-row API
  is a silent frontend regression; grep the web/ tree for every field a migrated query drops.
- 2026-10-07 (Claude, after Codex) — Tie-breakers are per reader and documented in each query:
  the API, reports, gold queue and killer chart take the lesser source_key when a satellite
  holds two keys of one source; the resolver takes the greater. Both are what the ledger
  readers did; neither is a decision about which key is "right".
- 2026-10-07 (Claude, after Codex) — Test fixtures own the feed's progress row inside their
  transaction (tests/claimfix.py overwrites it), so "last observed" is the test's, not the
  database's; the resolver test moves progress past a claim's first observation and closes a
  claim on a current key, and a mutant returning observed_from fails it.
