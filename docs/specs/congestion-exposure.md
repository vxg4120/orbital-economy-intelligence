# Spec: per-operator congestion exposure on the Operators view

**Status:** done
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-11-13

## Goal
`v_congestion_exposure` (SPEC §7.4, metrics/benchmark_views.sql) has computed a per-operator
congestion score since Phase 2, but nothing read it. The 2026-11-13 audit fixed it to leave out
reentered objects and applied it on production (16,997 objects in the field, down from 17,857).
Vib asked for the view to be made useful ("keep going on that congestion view that is useful").
This puts it on each operator's panel as plain sentences: how many other tracked objects share
the shell each of its satellites sits in, how many of those are its own, which other operators
supply most of the rest, and the shell holding the most of its objects. The split is the
finding: on 2026-11-13 a Starlink
satellite shared its shell with about 2,894 objects, 2,755 of them SpaceX's own, while a Planet
Labs satellite shared its shell with about 963 objects from other operators.

## Architecture decisions
- 2026-11-13: read the metrics view, not a second copy of its binning in the API. Rejected:
  recomputing the bins in api/routers/operators.py from mv_latest_gp_element. Because: the view
  is the documented metric, and one definition cannot drift from another. The cost is that a
  view change reaches production only through scripts/apply_metrics.py, which the nightly does
  not run (see Constraints).
- 2026-11-13: compute every operator's block in one query and keep it in the warm cache
  (api/cache.py), served from the detail endpoint. Rejected: a per-request query filtered to one
  operator. Because: the view aggregates the whole field on every read either way, and the
  data moves twice a day.
- 2026-11-13: report "objects sharing a shell per satellite" (a fleet-weighted mean of the bin
  counts) rather than the view's exposure score (a share of the field). The two carry the same
  information (score = (neighbours + 1) / field size), and a count reads as a fact. The score is
  still in the API for anyone who wants the documented metric.

## Constraints
- Copy states the limits every time it states a number: a shell spans the whole Earth, so this
  is density and not collision risk, and the field is mostly CelesTrak's active list, so debris
  and spent rocket bodies are almost entirely missing.
- Full sentences with the connective words kept; no en or em dashes in added lines.
- No new migration. The view's columns do not change.

## Interfaces & dependencies
- GET /api/operators/{id} gains `congestion`: null when none of the operator's objects is in
  the field, otherwise
  `{fleet_in_field, shells, neighbours_per_satellite, own_per_satellite,
  others_per_satellite, unattributed_per_satellite, top_neighbours: [{operator_id,
  canonical_name, per_satellite}] (at most 3), main_shell: {alt_km_from, alt_km_to,
  inc_deg_from, inc_deg_to, operator_objects, objects, tied_shells, tied_crowded_shells},
  exposure_score, field_objects}`.
  `top_neighbours` is ranked by `per_satellite`, the mean over the operator's objects of how
  many of the neighbour's objects share that object's shell (not the neighbour's raw count in
  its shells). `main_shell` is the shell holding the most of the operator's objects; among ties,
  the most crowded.
- Reads v_congestion_exposure (metrics/benchmark_views.sql). A change to that view is applied on
  production by hand: run its one CREATE OR REPLACE VIEW statement in a transaction.

## Edge cases
- An operator with no object in the field (all reentered, or no element sets): `congestion` is
  null and the panel says so.
- Objects in the field with no satellite row or no current owner count toward a shell's total
  but belong to no operator; they appear as `unattributed_per_satellite`, so own + named others
  + unattributed = neighbours.
- A one-satellite operator alone in its shell has 0 neighbours, not an error.
- GEO: the 50 km by 5 degree band holds the whole ring, so GEO operators show hundreds of
  neighbours that sit at other longitudes. The whole-Earth caveat covers it.

## Acceptance criteria
- [x] `pytest tests/test_api_operators_congestion.py` passes: on TEMP tables, own + others =
      neighbours, the named neighbours plus unattributed equal others, the top list is ordered
      by share (an id order fails it) and capped at 3, the main shell is picked by the
      operator's own count (an order by shell size fails it), the field size counts a shell the
      view has no row for, and an operator outside the field gets null.
- [x] GET /api/operators/{SpaceX id} on production returns a congestion block whose
      own_per_satellite is most of neighbours_per_satellite (2,755 of 2,894 on 2026-11-13).
      Verified 2026-11-13 21:08 UTC on GET /api/operators/1: own 2,755.3 of 2,893.7, main shell
      450 to 500 km at 50 to 55 degrees with 4,057 of 4,177 objects. The panel prints "2,756 of
      them its own and 138 belonging to other operators" (largest remainder gives the spare unit
      to own, whose remainder .34 beats .32).
- [x] The operator panel renders the section for an operator in the field and the empty state
      for one outside it, and the parts of the split it prints add up to the total it prints at
      the precision shown (vitest).
- [x] Added lines contain no en or em dash.

## Open questions
- Whether to add a league column or sort for "shares its shells with others" (Vib).

## Decision log & lessons learned
- 2026-11-13 (Claude): the view was applied on production by its one statement in a
  transaction (rows 1,712 to 1,630, objects 17,857 to 16,997), not by apply_metrics.py, which
  re-applies every metrics file.
- 2026-11-13 (Claude, from a three-lens review workflow; Codex was out of quota): ten findings
  survived an adversarial check, all in copy and tests. Lessons kept here:
  - A rounded part must be printed at the precision it was rounded to. Round the total, its own
    part and the unattributed part at the precision shown, and derive the other operators' part
    from them, or the printed parts stop adding up ("2.0 of them its own" for a true 1.5).
  - "Belonging to others" must not include objects with no owner on record; they are shown as
    their own part.
  - Name a ranking by what it ranks. The neighbour list is a per-object mean, so the sentence is
    "of those belonging to other operators, the largest numbers come from", not "the operators
    with the most objects in its shells", which a raw count would answer differently.
  - `busiest_shell` was renamed `main_shell` before it shipped: it is chosen by the operator's
    own count, and "busiest" read as the most crowded shell.
  - "Has its shell to itself" only when the average is exactly 0. Below 0.05 on average, fewer
    than 1 in 20 of its objects can share a shell, and the copy says that instead.
  - A test seed must make the wrong formula give a different answer: the largest neighbour has
    the largest id, an unowned object sits in a shell of its own, and a neighbour's main shell
    is not its most crowded one. Each was checked by a mutant that now fails.
  - Refuted: a 500 from the detail endpoint when the view is missing. No supported environment
    lacks it.
- 2026-11-13 (Claude, second review pass by a fresh agent): the first fix still let the other
  operators' part absorb every rounding error, so it could show a part that does not exist or
  hide one that does. The parts are now rounded by largest remainder over the three raw parts
  (own, other operators, no owner on record), a part that is truly 0 never gets a unit, and
  "all" or "none" is said only of a true 0 ("nearly all" and "almost none" otherwise). The
  neighbour shares are shown at the split's precision and never above the part they come
  from. `main_shell` gained `tied_shells` and `tied_crowded_shells`, so a tie is stated and
  not hidden behind "the shell holding the most".
- 2026-11-13 (Claude, third pass by the same reviewer, which fuzzed 200,000 synthetic
  operators): below 1 per object a tenth of an object is too coarse to split ("almost none of
  them its own" for a true 40%), so the parts are given there as tenths of the total ("4 in 10
  of them its own") and the neighbours are named without numbers. Rounded neighbour shares
  could add up to more than their part, so when they would they are all rounded down instead.
  An operator whose objects are all alone gets no main-shell sentence, since no shell stands
  out.
