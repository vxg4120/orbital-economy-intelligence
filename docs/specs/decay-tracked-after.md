# Spec: a decay date the tracking contradicts

**Status:** done; every criterion met on the 2026-11-20 07:10 UTC nightly and checked again
after the 2026-11-21 19:10 UTC run. Decided by Vib on 2026-11-19 ("Yes sounds good go for"), built 2026-11-20 UTC.
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-11-22

## Goal
An element set is a measurement of an object in orbit. When an object's latest element set is
dated long after the decay date a source gives for it, the object had not decayed on that date,
and the card should not say that it had. Three objects on production read as DECAYED while
they are tracked: NORAD 56915 (a Starlink that GCAT records as reentered on 2026-05-07, which
SATCAT has as operational and whose elements put it at 482 km), 44547 (Yunhai-1 02) and 24946
(Iridium 33), both GCAT status C, "collided", whose parent bodies are still catalogued with
element sets.

## What the data says (production, 2026-11-20, read-only)
- 17,901 satellites have an element set, and 882 of them have a resolved decay date.
- 9 of the 882 have a latest element set dated after the decay date. The gaps in days are
  1, 1, 1, 1, 1 (three Starlinks and two Dragons, the ordinary lag between a reentry and the
  last element set published for it), 17 (Dragon C206.6), and then 196 (56915), 2,072 (44547)
  and 6,357 (24946).
- All three large gaps come from a GCAT decay claim that is the object's only decay claim.
- Measured per claim after the deploy (read-only, the resolver's own functions): the rule sets
  aside 12 GCAT claims. The other nine (NORAD 45699, 46349, 48332, 48584, 52534, 53796, 57430,
  58307, 59615) are GCAT dates about a year before SATCAT's, on objects tracked until SATCAT's
  date. SATCAT already outranks GCAT, so their resolved date and DECAYED status do not change;
  the card now marks GCAT's date as not used.
- NORAD 59615 explains 56915. GCAT gives 59615 "2025 May  7 1041" and 56915 "2026 May  7 1041",
  and SATCAT has 59615 decaying on 2026-05-07. The reentry is 59615's, entered a second time
  under the transposed number.

## Architecture decisions
- 2026-11-20. A decay claim is set aside when the latest element set is dated more than 30 days
  after it (`identity/decay_claims.py`, `tracked_after`, `TRACKED_AFTER_DAYS = 30`). Rejected:
  any element set after the date. Because: six real reentries have an element set 1 to 17 days
  after their decay date, and 30 sits between 17 and 196 with room on both sides. It is also
  the month the decay-message rule already uses.
- 2026-11-20. The rule is per claim and applies to every source. Rejected: GCAT only. Because:
  the argument (tracked means in orbit) does not depend on who gave the date, and a SATCAT date
  or a Space-Track message that it contradicts is as wrong as a GCAT one. It fires only on GCAT
  claims today.
- 2026-11-20. It runs after the phase filter and after the decay-message outvote, so those two
  rules see every claim and the `decay messages outvoted` count is unchanged.
- 2026-11-20. The source whose decay date is set aside does not vote DECAYED for the status
  either, and the status falls through to the next source. Rejected: changing the date alone.
  Because: GCAT leads the status precedence, so the card would read DECAYED with no decay date
  for an object at 482 km. This narrows the 2026-10-16 decision that GCAT status C maps to
  DECAYED: that note says nothing in orbit is left under a collided object's identity, and for
  44547 and 24946 the catalog still tracks the parent body. C stays DECAYED in
  `status_map.yml` for every collided object that is no longer tracked.
- 2026-11-20. A date that gives only a month or a year is measured from the last day of that
  span. Rejected: its parsed first day. Because: "2025?" parses to January 1, and an object
  that decayed in November 2025 would be contradicted by an element set from that October.
- 2026-11-20. The resolver reads the latest epoch from `mv_latest_gp_element`. Rejected: the
  live `gp_elements` hypertable. Because: the nightly refreshes the view right after the build,
  so it is twelve hours behind, which does not matter against thirty days, and it is 18
  thousand rows where the table is ten million.

## Constraints
- No claim is deleted or changed. (The resolver's own earlier DECAYED rows for a set-aside
  source are deleted from `satellite_status_history`; that table is resolver output.) The set-aside claim stays in the claim table, on the card and
  on the Conflicts page.
- No migration and no manual database write. The nightly's `build_graph` applies the rule.
- No deploy or rebuild during the nightly windows (07:05 to 07:30 and 19:05 to 19:30 UTC).

## Interfaces & dependencies
- `identity/resolve.py`: `_resolve_decay_date` returns `{satellite_id: sources}` for the claims
  set aside, and `_resolve_status` takes it. The stat is `decay_dates_tracked_after`.
- `scripts/build_graph.py`: the build summary prints
  `decay dates set aside, tracked after: N  (NORAD ...)` in refresh.log.
- `GET /api/satellites/{id}`: a set-aside decay_date claim carries `tracked_after: true`, and
  the card prints "not used: tracked in orbit after this date" under the value.
- Readers take the newest row of `satellite_status_history`. `_resolve_status` deletes the
  DECAYED rows a set-aside source resolved on earlier nights, so the status changes on the
  first build after the deploy whatever the feeds' timestamps are.

## Edge cases
- A GCAT phase date (Hubble's 1990 deployment, status ATT) is removed by the phase filter first
  and is marked `phase_end`, never `tracked_after`.
- An object with no element set is never contradicted. Most decayed objects have none.
- One source's date is contradicted and another's is not: the other's date is resolved, and the
  card still shows the disagreement between them.
- The set-aside source is the only one with a status: its old DECAYED rows are deleted and the
  status is left unresolved.
- A satellite holding two keys for one source: the skip is by satellite and source, so a stale
  date on one key also mutes a DECAYED status on the other. None of the three is such a case.
- The card reads the live element table, where the resolver reads the view, so the mark can
  differ from the resolver's call for twelve hours at the 30-day boundary. (Until 2026-11-22
  the card also picked the lesser key per source where the resolver picks the greater; the
  readers now pick the greater key too.)
- A source gives a stale decay date and an in-orbit status: the status still votes, since only
  a DECAYED reading is contradicted.
- The conflict readers in SQL (`CURRENT_DECAY_CLAIMS_SQL`: the Conflicts page, the DQ report,
  the review queue) do not apply the rule. Nine of the twelve set-aside dates disagree with a
  SATCAT date, and that stays a counted conflict, since the sources do disagree.

## Acceptance criteria
- [x] `pytest -q -W error tests/test_decay_tracked_after.py`: 18 pass. Eleven mutations of the
      rule each fail at least one test.
- [x] `pytest -q -W error` and `ruff check .` are clean; `npx vitest run`, `npx tsc --noEmit
      -p .` and `npm run build` pass in `web/`.
- [x] After the first nightly on the new code, refresh.log has
      `decay dates set aside, tracked after: 12  (NORAD 24946, 44547, 45699, 46349, 48332,
      48584, 52534, 53796, 56915, 57430, 58307, 59615)` and
      `decay messages outvoted: 1  (NORAD 48587)`.
- [x] After that nightly, `GET /api/satellites/{id}` for the three: `decay_date` is null, the
      GCAT decay claim has `tracked_after: true`, and the newest status is ACTIVE for 56915 and
      44547 (SATCAT `+`) and INACTIVE for 24946 (SATCAT `-`).
- [x] NORAD 65077 (the 17-day gap) keeps decay date 2026-01-15 and DECAYED, and so does 45699
      with 2026-03-28 (one of the nine whose GCAT date loses to SATCAT anyway).
- [x] Live 2026-11-20 03:05 UTC (space 6f5a8b8): the card API marks the GCAT date
      `tracked_after` on 24946, 44547, 56915 and 45699, and leaves 40452 and 65077 as
      `phase_end`. Every site answered 200 after the rebuild.

## Open questions
1. Report the 56915 and 59615 transposition, and the nine year-early dates, to GCAT? Vib.
2. 35 objects have a decay date while SATCAT reports them in orbit with no decay date, and most
   have no element set to settle it. This rule does not reach them. Vib.

## Decision log & lessons learned
- 2026-11-20 (Claude). Measure a per-claim rule per claim. The first measurement compared the
  resolved date with the latest epoch and found three objects; the rule looks at every claim
  and finds twelve, because nine stale GCAT dates were already losing to SATCAT. The outcome
  for those nine is right, but the count in the log line would have looked like a fault.
- 2026-11-20 (Claude reviewer, verified by Claude). Skipping a vote is not enough in a table
  where the newest row wins. The first cut inserted the fall-through row and left the old
  DECAYED rows, which stayed newest whenever SATCAT's timestamp trailed GCAT's (it does: SATCAT
  loads first) and for ever when nothing fell through. The tests missed it because every
  seeded satellite began with an empty history. They now seed earlier DECAYED rows dated after
  everything else and read the status the way the readers do.
- 2026-11-20 (Claude). For GCAT, a set-aside date always sits beside a DECAYED status, because
  the phase filter has already removed every GCAT date whose status is anything else. A test
  that tried to show "a non-decay status still votes" with a GCAT claim passed for the wrong
  reason, and a mutation caught it. The case is real only for a source without a phase filter,
  so the test uses SATCAT.
