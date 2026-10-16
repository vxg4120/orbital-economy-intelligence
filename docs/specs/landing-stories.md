# Spec: Landing stories for Orbital and ExoDossier

**Status:** active
**Owner:** Vib
**Repos touched:** space (web/, api/), exodossier (web/, api/)
**Branch:** `landing/stories-20261016` in both repos (worktrees `space-wt-stories`, `exodossier-wt-stories`)
**Last updated:** 2026-10-16

## Goal
Vib asked for landing pages that "use the data to tell stories of what's happened/happening
recently and other interesting summary stats laid out in a more easily digestible way." Today both
Overview views open on resolver internals (coverage, crosswalk rows, merge events, the ingestion
ledger). A visitor who doesn't know the project can't tell what happened in orbit or in exoplanet
science this month. The new landing leads with a short written dispatch and a handful of story
panels, each one a headline sentence computed from the data, one chart or list, and a one-line
note on how the numbers were made. The existing technical panels stay, below a divider, as
"Under the hood".

## Architecture decisions
- 2026-10-16 (Claude): **One new read-only endpoint per app, `GET /api/stories`**, returning every
  story block in one payload, cached with each app's existing cache (`api/cache.py` WarmCache in
  space, RefreshingCache in exodossier, warmed at startup). Rejected: one endpoint per story
  (more round trips, more cache registrations, no gain since the data moves twice a day).
- 2026-10-16 (Claude): **`/api/stats` is untouched in both apps.** vibcreates.com reads it through
  Caddy `/live/orbital` and `/live/exo`, and `tests/test_api_stats.py` pins its keys.
- 2026-10-16 (Claude): **Headlines are computed server-side as numbers, and phrased client-side.**
  The API returns the facts (counts, dates, names); the SPA turns them into sentences, so copy
  changes never need an API deploy.
- 2026-10-16 (Claude): **Orbital constellations are keyed by resolved operator, not by name
  pattern.** Satellite names mix cases and formats across SATCAT and GCAT, so `ILIKE 'STARLINK%'`
  undercounts badly (499 vs 10,656). Operator display names (e.g. "Shanghai Yuanxin Weixing Keji
  YG" shown as "Qianfan (Shanghai Spacecom)") are a presentation map in the SPA. Exception:
  the reentry panel's "first-generation Starlink" count uses the name prefix `Starlink V1.0`,
  which is how GCAT names that cohort; it is labelled as a name match.
- 2026-10-16 (Claude): **Exo change stories diff the retained raw snapshots.** The exo graph is
  rebuilt nightly with TRUNCATE, so it has no history; the raw_* tables keep the first OK run of
  each month plus the newest three (scripts/prune_snapshots.py). Runs are looked up dynamically
  by date, never hard-coded ids.

## Constraints
- Read-only. No migrations, no writes, no change to ingest, nightly, Caddy or `/api/stats`.
- Don't touch the box. No deploy, no `docker compose` on the box; Vib reviews first.
- Keep each app in its current type system. Orbital: Fraunces narrates, mono testifies. Exo:
  all mono today, and space-7e is adding a Fraunces heading layer on
  `design/serif-headings-20261016`; this branch adds **no new font-family rules and no theme
  token changes** in exo. Reuse existing classes and tokens; new CSS goes in a clearly marked
  "Stories" block at the end of theme.css.
- Keep the tour anchors (`data-tour="stats"`, `"conflicts"` in orbital; `"stats"`, `"dramatic"` in
  exo) or update `lib/tour.ts` in the same commit so every step still finds its target.
- No new npm dependencies. Recharts stays out of the initial orbital bundle; charts are
  hand-rolled SVG or CSS bars like EnvironmentStrip and CongestionHeatmap.
- New copy: plain full sentences, no em or en dashes, no dropped connective words, no hype
  ("staggering", "explosive"). Every number on the page comes from the API.
- Honest about lag: the current month is marked partial; payloads whose owner is still only a
  country code (operator_class IS NULL) are counted as "awaiting company attribution", never
  silently dropped or guessed.
- Works at 390 px wide with no horizontal page scroll; respects reduced motion.

## Interfaces & dependencies

### Orbital `GET /api/stories` (space/api/routers/stories.py, WarmCache name `stories`)
```
{
  as_of: "YYYY-MM-DD",                    // max(finished_at) of ok ingest runs, UTC date
  launches_monthly: [{month: "YYYY-MM", launched: int, reentered: int, partial: bool}],  // 13 months, PAYLOAD only
  last_30d: {launches: int, payloads_launched: int, payloads_reentered: int},
  recent_launches: [{launch_key, date, lead_name, payloads, objects, owner, owner_attributed: bool}], // 10 newest launch groups
  reentries: {window_days: 30, payloads: int, starlink: int, starlink_v1: int,
              notable: [{name, date, launched, owner}]},     // 8 newest non-Starlink payload reentries
  constellations: [{key, operator, in_orbit_now, added_12m,
                    series: [{month: "YYYY-MM", in_orbit: int}]}],   // 24 month-ends
  attribution: {pending_payloads: int, since: "YYYY-MM-DD", complete_through: "YYYY-MM"},
  active_fleet: {total: int, top: [{operator, n, pct}], other: int},   // ACTIVE payloads via mv_latest_satellite_status
  storms: [{day, level, kp_max}]          // NOAA G1+ days, last 120 days, newest first
}
```
Launch groups use `oei_launch_key(cospar_id)` (migration 0013). Constellations: SpaceX, Eutelsat
(OneWeb), Amazon (Kuiper), Shanghai Yuanxin Weixing Keji YG (Qianfan), Zhongguo Weixing Wangluo
Jituan YG (Guowang). `complete_through` is the newest month in which at least 80% of launched
payloads have an owner with a non-null operator_class; constellation series stop there.

### Exo `GET /api/stories` (exodossier/api/routers/stories.py, RefreshingCache, warmed in lifespan)
```
{
  as_of: "YYYY-MM-DD",
  confirmed: {series: [{date, count}],              // one point per retained pscomppars snapshot
              baseline_date, added_since_baseline, removed_since_baseline,
              newest: [{pl_name, host, facility, radius_re, period_days, first_seen}],  // up to 12, newest first
              sizes: [{bin, count}],                // planets added since baseline, by radius bin
              facilities: [{facility, count}],      // planets added since baseline
              records: {smallest, shortest_period, longest_period}},  // among planets added since baseline
  toi_verdicts: {from_date, to_date, transitions: [{from, to, count}],
                 demoted: [{toi, tic_id, from, to}]},   // CP or KP that became FP/FA, all of them
  toi_verdicts_month: {from_date, to_date, transitions: [...]},   // vs the first snapshot of this month
  toi_mix: [{disposition, count}],                  // TFOPWG disposition in the newest snapshot
  toi_alerts_monthly: [{month, count}]              // "Date TOI Alerted (UTC)", 15 months
}
```
Radius bins: Earth-size < 1.25 Re, super-Earth 1.25 to 2, sub-Neptune 2 to 4, Neptune-size 4 to 6,
giant >= 6, unknown radius.

## Edge cases
- A source with fewer than two retained snapshots: exo change blocks return empty transitions and
  the SPA hides that panel rather than showing zeros as if nothing happened.
- The newest TOI snapshot can carry a blank disposition: shown as "no disposition yet".
- Recent launch groups whose payloads are attributed only to a country show the country with an
  "awaiting company attribution" marker.
- Planets in pscomppars that disappear between snapshots (2 did on Oct 1) are counted as
  `removed_since_baseline`, not hidden.
- Launch dates in the current month: `partial: true`, drawn hatched or faded with a label.

## Acceptance criteria
- [ ] space: `curl -s localhost:8600/api/stories | jq 'keys'` lists every key above, against the
      live DB through the read-only tunnel; cold compute under 5 s.
- [ ] exodossier: same for `localhost:8700/api/stories`; cold compute under 5 s.
- [ ] `/api/stats` response keys unchanged in both apps (`tests/test_api_stats.py` passes in space).
- [ ] space: `ruff check .` clean; `pytest -q -W error -m "not db"` passes; new tests for
      `/api/stories` (shape test with the `db`/`graph` markers, plus DB-free tests of any pure
      helpers).
- [ ] exodossier: `ruff check .` clean; new DB-free cache test for stories in the style of
      `tests/test_landing_cache.py`; `pytest -q -W error` passes for every test that doesn't need
      a DB.
- [ ] `pnpm -C web build` passes in both repos (strict tsc plus vite).
- [ ] Mock mode works: `VITE_API_MOCK=1` serves a new `fixtures/stories.json` in both SPAs.
- [ ] At 1280 px and 390 px the landing renders every story panel with live data, no horizontal
      page scroll (`document.documentElement.scrollWidth <= innerWidth`), no console errors.
- [ ] Tour: every step's selector resolves on `/`.
- [ ] `grep -nP '[\x{2013}\x{2014}]'` finds no en or em dash in any line this branch adds.

## Open questions
- Vib: should the old technical panels stay on the landing under "Under the hood", or move to an
  About/Method view? (Default: stay, below the stories.)
- Vib: the vibcreates.com root page could later read one or two story headlines through new
  `/live/*` routes. Out of scope for this branch.

## Decision log & lessons learned
- 2026-10-16 (Claude) Since mid-July nearly every newly launched payload is owned in the graph by
  a country-level operator (operator_class NULL, e.g. "United States") because GCAT attributes
  companies about three months late. Any "who launched what" or constellation-growth number must
  stop at `complete_through` or show the pending count, or SpaceX looks like it shrank (10,774 in
  July to 10,656 in October in a naive count).
- 2026-10-16 (Claude) Exo has no disposition history table; the retained raw snapshots are the only
  history (2026-07-15 baseline, the first of each month, and the newest three daily pulls).
