# Spec: one GCAT key, one satellite (the double-linked jcats)

**Status:** active. Vib: "go for it, do what you think is best quality" (2026-10-06); the open
questions below are decided as recorded.
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
- 2026-10-06 (after Codex verify) — **A key is judged only when GCAT gives it exactly one
  anchor.** Two rows with different NORADs and the same piece would otherwise retire each
  other's cospar links and leave none, revived nightly by the matcher.
- 2026-10-06 (after Codex verify) — **The COSPAR matcher applies the same authority**: a
  NORAD-less row whose key is `S<n>` links to the satellite with NORAD n (rule `jcat_number`)
  before any piece lookup. Without it the ISS rows would be re-linked to the neighbour by piece
  every morning and retired by churn every night.
- 2026-10-06 (after Codex verify) — **A current claim is a claim through a current key.** The
  resolver, the satellite page and the conflicts page read `v_linked_assertion` (migration
  0022): `source_assertion` rows whose (source, source_key) still identify the satellite.
  History stays in the table; a sibling's leftover claim stops counting the moment its link is
  retired, including for an attribute the satellite's own key never asserts, and a satellite
  that loses its only GCAT key shows no GCAT claims rather than stale ones. Rejected: relying
  on the writer's `valid_to` filter alone. Because: newest-per-key readers would keep showing
  the sibling's row wherever the own key is silent (Codex verify).
- 2026-10-06 (after Codex verify) — **Revival is audited**: both resurrection paths write an
  `identifier_revived` event (migration 0022 extends the event vocabulary).
- 2026-10-06 — **`name_gcat` links are not judged**: co-deployed siblings legitimately share
  names, so a name is not a key.

## Constraints
- No deletion from `satellite_identifier`; every change sets `valid_to` and writes an
  `identity_event`.
- The rule never touches a key whose GCAT row has no NORAD and whose number matches none of
  its satellites (0 today); such keys stay with `expire_contested`.
- The nightly after the change must pass both gates. For rows that carry a NORAD the bus build
  is unchanged (rule 1 bypasses the crosswalk). For NORAD-less rows its rule 2 takes the
  lowest current anchored cospar link, so retiring a sibling's cospar link can move an
  attribution to the key's own satellite; that is the convention applied, measured after the
  first nightly against the saved before-state (`zz_bus_before` on the box).
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
  returns 0 rows (105 today), with one `identity_event` per retired link (107 losing `gcat_id`
  links, plus whichever cospar twins the anchor holds).
- [ ] For NORAD 65729 and 65730, `/api/satellites/<id>` shows GCAT claims only from the
  satellite's own key after the next nightly (today each shows the sibling's).
- [ ] The bus build's `satellite_bus` rows for the 129 affected satellites, compared with the
  before-state: unchanged for NORAD-carrying rows; every change on a NORAD-less row moves the
  attribution to the key's own satellite.
- [x] Tests (tests/test_gcat_sibling_links.py, 9 of them): a moved key is retired with its
  event; the key-number fallback; nothing retired when the anchor lacks the key; a shared piece
  judges nothing; the COSPAR matcher links by number; a full move-and-move-back round trip
  with its three events; a retired link receives no claim; a claim through a retired link is
  not current. Mutants of the direction, the guard, the fallback, the unique-anchor rule, the
  matcher authority and the writer filter each fail the suite. Full suite 408 passed.
- [ ] Both nightly gates pass on the first run after deployment.

## Open questions
- Decided 2026-10-06 (Claude, under Vib's "best quality"): the `S<n>` convention is
  authoritative, it agrees with the data for all 23 NORAD-less keys and is how GCAT defines
  S-numbers; cohorts that the corrected links restore come back live, the structural gate
  already handles live against archived; `name_gcat` links stay as they are and are not judged.

## Decision log & lessons learned
- 2026-10-06 (Codex verify, confirmed by Claude) — the first implementation could retire every
  link of a piece shared by two anchors, fought the COSPAR matcher nightly on the ISS rows,
  revived links without an event, and left a sibling's leftover claim visible wherever the
  satellite's own key was silent. Each was reproduced in a test before the fix, and the "current
  claim through a current key" view came out of it.
- 2026-10-06 (Claude) — Spec drafted from a code trace of the three `gcat_id` writers, churn's
  retirement rule and bus.py's anchoring, plus read-only measurements on production. The core
  lesson: an additive crosswalk needs a retirement rule from day one, or every catalog revision
  leaves a wrong link behind with confidence 1.00.
