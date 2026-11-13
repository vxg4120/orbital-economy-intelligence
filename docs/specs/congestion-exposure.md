# Spec: per-operator congestion exposure on the Operators view

**Status:** active
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
supply most of the rest, and its busiest shell. The split is the finding: on 2026-11-13 a Starlink
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
  canonical_name, per_satellite}] (at most 3), busiest_shell: {alt_km_from, alt_km_to,
  inc_deg_from, inc_deg_to, operator_objects, objects}, exposure_score, field_objects}`.
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
- [ ] `pytest tests/test_api_operators_congestion.py` passes: on TEMP tables, own + others =
      neighbours, the named neighbours plus unattributed equal others, the top list is ordered
      and capped at 3, the busiest shell is right, and an operator outside the field gets null.
- [ ] GET /api/operators/{SpaceX id} on production returns a congestion block whose
      own_per_satellite is most of neighbours_per_satellite (2,755 of 2,894 on 2026-11-13).
- [ ] The operator panel renders the section for an operator in the field and the empty state
      for one outside it (vitest).
- [ ] Added lines contain no en or em dash.

## Open questions
- Whether to add a league column or sort for "shares its shells with others" (Vib).

## Decision log & lessons learned
- 2026-11-13 (Claude): the view was applied on production by its one statement in a
  transaction (rows 1,712 to 1,630, objects 17,857 to 16,997), not by apply_metrics.py, which
  re-applies every metrics file.
