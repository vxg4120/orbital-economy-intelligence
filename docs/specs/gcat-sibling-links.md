# Spec: one GCAT key, one satellite (the double-linked jcats)

**Status:** draft, for Vib's decision on the surviving-link rule
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-10-06

## Goal
On production, 105 GCAT keys (`gcat_id`, values like `S65730`) are each linked to two or three
satellites in `satellite_identifier`, every link current (`valid_to` NULL, 212 links; NORAD
links have no duplicates). They are satellites deployed together, 30 of them ISS-deployed
cubesats of the 1998-067 COSPAR family and 93 launched in 2026, whose provisional
identifications GCAT revised after our first link. `identity/assertions.py` joins the crosswalk
per link, so each sibling receives the other's GCAT claims (owner, status, name, decay date, bus,
manufacturer), the Resolver and conflicts pages show them, and six 2-satellite manufacturer
cohorts retired on 2026-09-28 for this reason. The bus benchmarks are immune because
`identity/bus.py` anchors each GCAT row to the satellite whose NORAD it carries today. The goal
is that rule everywhere: a GCAT key links to exactly one current satellite.

## What the data says (production, 2026-10-06, read-only)
- All 212 links were written by the GCAT matcher with confidence 1.00; every linked satellite is
  `anchored`.
- For every one of the 105 keys, exactly one linked satellite has the NORAD number the key is
  named after (`S65730` and NORAD 65730); the other 107 links point at a deployment sibling.
- GCAT's newest row for the key carries a NORAD for 82 keys, and in all 82 it is the key's own
  number. The other 23 rows (the ISS batch) carry no NORAD, and their COSPAR piece is one letter
  off SATCAT's for the same object (GCAT `1998-067VR` where SATCAT says `VQ`), which is exactly
  how a COSPAR match lands on the neighbouring cubesat.
- 190 of the links date from the first build in July 2026, 20 from September.

## Architecture decisions
- 2026-10-06 — **The surviving link for a key is the satellite whose NORAD equals the NORAD
  on GCAT's newest row for that key** (`identity/bus.py` rule 1, `anchored_norad`). Rejected:
  trusting the crosswalk's first link. Because: the NORAD pass in `identity/match.py` is
  additive by contract (`_bulk_link_by_norad`, `ON CONFLICT DO NOTHING` per satellite) and
  never retires, so after GCAT moves a key the older link is simply wrong.
- 2026-10-06 — **For keys whose GCAT row carries no NORAD, the key's own number decides**
  (`S<n>` is catalog number n in GCAT's scheme). On production this agrees with the data for
  all 23 such keys, and it is the only evidence that ties the key to one sibling, since the two
  catalogs disagree on the COSPAR piece. Open question 1 asks Vib to confirm the convention.
- 2026-10-06 — **Retire, don't delete.** The losing link gets `valid_to` = the latest OK run's
  date and an `identity_event` row (`identifier_expired`, reason `gcat_anchor_moved`), as
  `identity/churn.py` does for contested provisional keys. Rejected: deleting. Because: the
  audit trail of what we once believed is the product's premise ("no silent merges, ever").
- 2026-10-06 — **Three companion changes are mandatory**, or the retirement changes nothing:
  `identity/assertions.py` must join the crosswalk with `valid_to IS NULL` (it does not
  today, so a retired link would keep receiving claims every night); `_bulk_link_by_norad`
  needs the resurrection `UPDATE ... SET valid_to = NULL` that `identity/merge.py` already has,
  so a key GCAT moves back is revived rather than left permanently retired; and the same rule
  applies to the `cospar/gcat` and `name_gcat` links written beside the `gcat_id` one.
- 2026-10-06 — **Placement: a new pass in `identity/churn.py` inside `run_all`**, after
  promotion and before assertion extraction, set-based, one statement per key type. Not in
  `match.py`: prevention there is additive-only by design and would not fix the 105 existing
  rows.

## Constraints
- No deletion from `satellite_identifier`; every change sets `valid_to` and writes an
  `identity_event`.
- The rule never touches a key whose GCAT row has no NORAD and whose number matches none of
  its satellites (0 today); such keys stay with `expire_contested`.
- The nightly after the change must pass both gates, and the bus build must attribute exactly
  the same satellites as before (its anchoring already encodes the rule).
- Historical `source_assertion` rows written under the wrong sibling are left in place; the
  newest-per-key readers heal on the first extraction after the change, since the sibling's
  own key then holds the newest claim. The assertion-model spec removes them for good.

## Interfaces & dependencies
- `identity/churn.py` (new pass, `run_all`), `identity/match.py` (`_bulk_link_by_norad`
  resurrection), `identity/assertions.py` (`valid_to IS NULL` on the crosswalk join).
- Readers that show retired keys and should filter them: `api/routers/satellites.py`
  (identifier list; shows `valid_to`, acceptable), `scripts/build_gold_queue.py` (takes the
  first `gcat_id` by value with no `valid_to` filter; must filter).
- `quality/report.py` counts expired `gcat_id`/`cospar` rows; the 105 will appear there once,
  which is correct.
- Depends on nothing in docs/specs/assertion-model.md, but that spec depends on this one:
  fix the links first, or the new claims table inherits the duplication.

## Edge cases
- GCAT swaps a pair back: the resurrection path revives the retired link and retires the other,
  with two `identity_event` rows; nothing is lost.
- A key moved to a satellite that does not exist yet (new NORAD not in SATCAT): no surviving
  candidate, so nothing is retired that run; the matcher creates the link once the satellite
  exists.
- The 20 September links show the revision is ongoing; the pass runs nightly, not once.
- A satellite that loses its only `gcat_id` keeps its SATCAT claims; its GCAT-only claims
  (bus, manufacturer) stop, which is what the bus build already does for it.

## Acceptance criteria
- [ ] After the pass on a production snapshot, `SELECT id_value FROM satellite_identifier WHERE
  id_type = 'gcat_id' AND valid_to IS NULL GROUP BY 1 HAVING count(DISTINCT satellite_id) > 1`
  returns 0 rows (105 today), and 105 `identity_event` rows with reason `gcat_anchor_moved`.
- [ ] For NORAD 65729 and 65730, `/api/satellites/<id>` shows GCAT claims only from the
  satellite's own key after the next nightly (today each shows the sibling's).
- [ ] The bus build's `satellite_bus` rows for the ~210 satellites are byte-identical before and
  after (its anchoring already encodes the rule).
- [ ] A test seeds a key moved between two anchored satellites across two runs and asserts the
  older link is retired, the event is written, and moving it back revives it.
- [ ] `identity/assertions.py` with `valid_to IS NULL`: the pipeline test's assertion counts are
  unchanged on its single-run fixture, and a new test shows a retired link receives no claim.
- [ ] Both nightly gates pass on the first run after deployment.

## Open questions
- (Vib) Confirm the convention that `S<n>` is catalog number n is authoritative for the 23
  NORAD-less keys, or leave those 23 to retire only when GCAT publishes a NORAD.
- (Vib) Whether the six retired manufacturer cohorts (asc24, kansai, munf, rhodes, unbrun,
  wiss) should come back live if the corrected links restore them, or stay archived.
- (Claude) Whether `name_gcat` links carry enough value to keep at all once `gcat_id` is
  unambiguous.

## Decision log & lessons learned
- 2026-10-06 (Claude) — Spec drafted from a code trace of the three `gcat_id` writers, churn's
  retirement rule and bus.py's anchoring, plus read-only measurements on production. The core
  lesson: an additive crosswalk needs a retirement rule from day one, or every catalog revision
  leaves a wrong link behind with confidence 1.00.
