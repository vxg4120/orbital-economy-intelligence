# Bus Benchmarks Methodology

**Version 1.5, updated 2026-07-31.**

This document is the normative definition of every number the Bus Benchmarks feature publishes
(the `/api/buses` endpoints, the BUSES view of the Orbital Terminal, and the `bus_benchmarks` /
`bus_detail` MCP tools). Any change to a metric definition, threshold, inclusion rule, or
attribution rule bumps the version here and in the API (`/api/buses/methodology` returns the
current version), and is recorded in the Changelog at the bottom. Monthly snapshots store the
version that produced them, so any published figure can be interpreted against the exact rules
that were in force when it was computed.

## 1. Purpose

Bus Benchmarks is an independent, provenance-tracked performance scoreboard for satellite buses
(spacecraft platforms), aggregated two ways: by manufacturer and by bus model. It answers
questions the trade press and procurement teams usually answer with anecdote: how large is a
platform's flown fleet, how quickly do its satellites reach their operational orbit, what share
of them actively hold station, how long do they live, and how do they end.

It is built entirely from public data, computes behavior from orbital element history rather
than from vendor claims, and publishes every definition, denominator and caveat. Where the data
cannot support a number, the number is absent and the coverage column says why.

## 2. Data sources

| Source | Role | Reference |
|---|---|---|
| GCAT satcat (Jonathan McDowell) | Bus model and manufacturer attribution per cataloged object (`Bus`, `Manufacturer` columns) | https://planet4589.org/space/gcat/ |
| GCAT orgs | Organization registry: resolves manufacturer codes to organizations, with class and parent hierarchy | https://planet4589.org/space/gcat/data/tables/orgs.html |
| GP element history (Space-Track, CelesTrak) | Orbital behavior: daily semi-major-axis series behind station-keeping, orbit-raising and decay metrics | https://celestrak.org/NORAD/elements/ and https://www.space-track.org |
| OEI identity graph | The satellite crosswalk (GCAT ids to canonical satellites), canonical status history, and resolved decay dates | this repository, `db/migrations/0004_identity.sql` |

GCAT is used under its license terms as the attribution source; the behavioral layer is computed
independently in this repository from GP element sets aggregated into the `sat_daily` daily
series (`metrics/caggs.sql`).

## 3. Inclusion criteria

* Payload objects only: GCAT object type beginning with `P`. Rocket stages and debris are
  excluded even where GCAT records a platform for them, because bus benchmarking is about
  spacecraft platforms.
* An object enters the benchmark when the latest OK GCAT snapshot attributes a bus model
  and/or a manufacturer to it, and the object resolves to a canonical satellite under the
  three-rule join described in section 4.1: by its permanent NORAD id when the row carries
  one, else through the COSPAR crosswalk to an anchored satellite, else as provisional slot
  occupancy. Neither of the catalogs' provisional keys is stable on fresh launches (GCAT
  renumbered 67 of 73 Transporter-17 catalog ids against their pieces in one release, then
  re-identified 41 of 73 slot occupants in another without moving any key), which is why the
  permanent id outranks every crosswalk and the join rule is published per row.
* Counting convention: this scoreboard counts spacecraft that have launched and been
  catalogued. It does not count buses that a manufacturer has built or delivered but that have
  not flown, and it does not count hosted payloads riding on someone else's bus as separate
  spacecraft. A manufacturer's own published total may therefore exceed the fleet shown here.
* Provisional identifications: a spacecraft that has not yet been assigned a permanent NORAD
  id is identified by the catalog provisionally, and that identification can change. Between
  the releases of 2026-07-21 and 2026-07-27, GCAT re-identified which satellite occupies 41 of
  the 73 Transporter-17 slots without moving any key. Fleet counts that include such objects
  are snapshot statements rather than settled ones. As of v1.5 the split is published:
  `provisional_n` counts such rows per cohort, each row carries its `join_rule`, and
  `state=anchored` serves anchored-only aggregations on request.
* Cohort floor: leaderboards default to cohorts of at least 5 satellites (`min_n=5`). The floor
  is a presentation-layer filter, configurable per request down to 1; the views themselves
  aggregate every cohort so the floor never changes stored numbers.

Build implementation: `identity/bus.py` (rules), `scripts/build_bus.py` (runner),
`db/migrations/0009_satellite_bus.sql` (schema).

## 4. Attribution and resolution rules

### 4.1 Manufacturer resolution

* GCAT's `Manufacturer` column carries an org code (for example `SPXS`, `CALT`). Codes resolve
  against the latest GCAT orgs snapshot (`raw_gcat_orgs`).
* Co-manufactured objects (`NPOL/KOMET`) attribute to the first-listed org. This is our
  convention and not a GCAT semantic, and earlier versions of this document wrongly presented it
  as one. GCAT does not document what the slash means or whether order is significant, and in the
  2026-07-27 snapshot 8 org pairs appear in both orders (`AEROAC/BALL` alongside `BALL/AEROAC`),
  which is not consistent with position encoding primacy. The full code list is preserved per
  satellite in `manufacturer_codes`, so a co-builder is recorded even when it is not credited,
  and the leaderboard consequently understates organizations that are frequently listed second.
  For the Terran Orbital group of codes the difference is 56 satellites credited against 79
  recorded, so the shortfall is real and measurable rather than hypothetical.
* A `?` marks the attribution uncertain in GCAT. On a compound value the marker attaches to the
  individual code (`RAYM?/GSFC`) rather than to the end of the string, so it is stripped per code.
  The marker is stripped for grouping and stored as a boolean flag (`manufacturer_uncertain`), so
  uncertain rows are counted but visibly flagged, never silently promoted to certain.
* After the GCAT rollup, an operator-identity layer merges cohorts whose rolled-up group codes
  resolve to the same organization in the platform's curated operator graph. The layer is
  strictly merge-only: it can join cohorts that are the same real-world company under different
  GCAT codes, and it structurally cannot split one, because it keys on the rolled-up group code
  rather than the leaf. When cohorts merge, the surviving slug and display name belong to the
  incumbent group code with the largest fleet, with ties broken by code order; that rule is a
  published-URL commitment, pinned by tests. Every slug the merge retires gets a permanent
  redirect recorded in `benchmark_slug_alias`, so `/buses/{old}` keeps serving the surviving
  cohort with an explicit `aliased_from` field, and the retired slug's frozen monthly series
  names its continuation. Group codes the operator graph does not recognize keep their incumbent
  cohort untouched.
* Every attributed satellite records how its catalog row was joined to its canonical
  identity, published per row as `join_rule` with three values. `anchored_norad` means the
  catalog row itself carries the permanent NORAD id, a key that has never been observed to
  move (the platform measures this nightly and publishes the measurement in its data quality
  report). `anchored_cospar` means the row reached an anchored satellite through the COSPAR
  crosswalk. `provisional_slot` means the satellite has no permanent id yet, so the
  attribution is a dated observation of slot occupancy rather than a settled identity.
  Provisional fleets stay on the leaderboard, counted per cohort in `provisional_n`, because
  they are the newest and most commercially interesting cohort and the catalogs' name-level
  attribution is stable even while slot assignments move; readers who want settled
  identifications only can pass `state=anchored`, which excludes provisional rows from the
  aggregation. A companion flag, `key_churn_observed`, marks joins that rode a key the
  platform's churn ledger has seen reassigned.
* Manufacturer rollup is undated, current-state attribution. A satellite is credited to the
  corporate group that owns the factory today, not the one that owned it on build day, because
  no build date exists in any source catalog and launch dates postdate builds by months to
  years. The build never reads the operator graph's dated relationship edges, whose dates are
  founding dates rather than acquisition dates.

### 4.2 Parent rollup (manufacturer grouping)

Manufacturer leaderboard rows are grouped at the corporate-group level, resolved as follows:

* Follow the GCAT orgs `Parent` chain upward from the attributed org, but only through parents
  whose GCAT `Class` is `B` (business). The walk stops before any non-business parent.
* Effect: plant-level subsidiaries roll up to their corporate group (Boeing El Segundo `BOES`
  rolls to Boeing `BOE`; Kuiper Systems `KUIP` rolls to Amazon `AMAZ`), while state design
  bureaus and academies do not collapse into ministries or space agencies (NPO PM stays NPO PM
  rather than becoming the Soviet ministry `MOM`; RKK Energiya does not become Roskosmos;
  Chinese academy-level manufacturers such as CALT and SAST stay at academy level rather than
  collapsing into CASC).
* Curated overrides: exactly one, `SPXS` (SpaceX Seattle, GCAT's code for the Starlink
  manufacturing works, which GCAT leaves unparented) rolls up to `SPX` (SpaceX). Satellites
  resolved through an override carry `rollup_source = 'gcat_orgs+override'`; the full traversal
  path from leaf code to group code is stored per satellite (`rollup_path`).
* Display names prefer the orgs short name, then the English name, then the native name.
  Cycle-guarded, depth-capped at 10.

### 4.3 Bus model normalization

GCAT bus strings carry formatting variants; normalization is deliberately conservative:

* Whitespace collapsed and trimmed; a leading apostrophe (a GCAT name-formatting marker) is
  stripped.
* A trailing `?` (uncertain) is stripped and recorded as `bus_uncertain`.
* Placeholder values (`UNK`, `Unknown`, `TBA`, `None`) are dropped, not benchmarked as models.
* Casing and punctuation variants of the same model collapse to one entry keyed by a slug
  (lowercase, non-alphanumerics to `-`), displayed with the most common source spelling
  (`Corvus-Micro` and `Corvus Micro` merge). The `+` character is load-bearing in bus names
  (`BSS-702MP+` is a different variant from `BSS-702MP`) and is preserved in the key as
  `-plus`.
* Genuinely distinct variants are never merged: Starlink `V2M` and `V2MO` remain separate
  models. No family-level grouping is imposed beyond what the source string states.

## 5. Metric definitions

All behavior metrics reuse the operator benchmark machinery in `metrics/benchmark_views.sql`
verbatim; the bus layer only re-aggregates the same per-satellite results by manufacturer and by
bus model (`metrics/bus_benchmarks.sql`). Every metric ships with its cohort size `n`.

### 5.1 Fleet size

`fleet_total` counts attributed payloads. `fleet_on_orbit` counts those whose latest canonical
status (from `satellite_status_history`) is not `DECAYED`; `fleet_active` counts status
`ACTIVE`. The same rule the platform's operator league table uses.

### 5.2 Median time to operational (`median_days_to_operational`, n = `tto_n`)

From `v_time_to_operational`: a LEO payload is deemed operational on the first day of its first
streak of 7 consecutive observed days within 15 km of its constellation shell's median stable
semi-major axis; the metric is days from launch to that day. Cohort restricted to shells with
at least 3 members, exactly like the per-operator rollup. Satellites that never acquire their
shell are omitted, not counted as zero. LEO-only and limited to launches inside the GP history
window by construction.

### 5.3 Station-keeping share (`station_keeping_share_pct`, n = `sk_n`)

From `v_station_keeping_30d`: for each currently ACTIVE payload with GP history, take the
median of its rolling 30-day standard deviation of daily mean semi-major axis. A satellite
counts as station-keeping when that median is at or below 0.100 km, an empirically clean
separator between actively held orbits (Starlink medians near 0.04 km, OneWeb near 0.005 km)
and passively drifting spacecraft (Planet Doves near 0.7 km). The share is over `sk_n`, the
behavior-observed ACTIVE cohort, never over the whole fleet.

### 5.4 Station-keeping tightness (`p50_station_keeping_km`)

The cohort median of the same per-satellite medians, in km. Lower is tighter. Reported to 4
decimal places because the interesting differences are meters.

### 5.5 Decayed share and median lifetime (`decayed_share_pct`, `median_lifetime_years`, n = `lifetime_n`)

Decayed share is the fraction of the attributed fleet whose latest canonical status is
`DECAYED`. Median lifetime is the median of (decay date minus launch date) over decayed
payloads with both dates present. This is a survivor-censored statistic: satellites still
flying contribute nothing, so young fleets show short medians (early failures only) and the
number must be read together with `decayed_share_pct`.

### 5.6 Post-mission disposal, 5-year rule (`disposal_compliance_pct`, n = `disposal_n`)

From `v_deorbit_compliance`: for payloads whose latest status is `DECAYED` or `INACTIVE`,
compliant means the decay date falls within 5 years of the last observed ACTIVE status (the
FCC-style post-mission disposal rule). Only decidable rows (both dates known) enter the rate.
This view is currently sparse (single-digit decidable verdicts platform-wide) and will fill in
as decay-message backfill deepens; until then most cohorts publish `n = 0` and no rate, which
is the honest answer.

### 5.7 Behavior coverage (`gp_coverage_pct`, n = `gp_n`)

The share of the attributed fleet with at least one day in the `sat_daily` GP aggregate. This
is the denominator-honesty metric: every behavior metric above is computed only over observed
satellites, and this column says how large that slice is. Coverage is strongly LEO-biased
(the GP history backfill prioritized LEO); a GEO-heavy manufacturer with near-zero coverage has
near-zero behavior metrics not because its buses do nothing but because we do not observe them
yet.

## 6. Known limitations and biases

**Manufacturers and bus models share one URL namespace.** `/buses/{slug}` resolves manufacturers
before bus models, and 29 slugs currently exist in both groupings. Where they collide the
manufacturer page is served, so `/buses/arrow` shows the Arrow Sci Tech manufacturer rather than
the ARROW bus model, which is the larger cohort of the two. The resolution order is fixed rather
than chosen per request, because changing it would relocate already published URLs. To keep the
shadowed cohort reachable, every detail response carries an `also_exists_as` field naming the
other cohort with the same slug and the URL that resolves it, and `?kind=manufacturer` or
`?kind=bus` pins the grouping explicitly. A slug is therefore a URL key, not an entity
identifier, and it should not be treated as one.


* **LEO observation bias.** GP element history coverage is far deeper for LEO than GEO/MEO.
  Time-to-operational is LEO-only by construction. Compare GEO manufacturers on behavior
  metrics with care and always read `gp_coverage_pct` first.
* **Attribution completeness.** GCAT attributes a manufacturer to roughly 27,850 of about
  27,930 cataloged payloads in the current snapshot, and a bus model to about 27,500; the
  remainder are absent here. GCAT's own uncertainty markers are preserved per satellite.
* **Survivorship effects.** Lifetime medians are over decayed objects only; long-lived
  satellites still flying are censored. Fleets mid-deployment show deceptively low lifetimes.
* **Ambiguous variants.** Bus naming in the wild is messy. Normalization collapses only
  formatting variants; where GCAT distinguishes variants the benchmark does too, even when the
  industry sometimes lumps them.
* **Generic form factors.** Entries like `Cubesat 3U` aggregate many unrelated vehicles from
  many builders. They are kept (fleet counts are meaningful) and labeled with their most common
  manufacturer, but behavior metrics for them describe the population, not a product.
* **Status is resolved, not gospel.** On-orbit/decayed classification comes from the identity
  graph's canonical status resolution across catalogs; conflicts between sources exist and are
  surfaced in the platform's Conflicts view rather than hidden.

## 7. Update cadence and snapshots

The attribution and behavior layers rebuild nightly with the daily ingest cycle
(`scripts/daily_ingest.sh` runs `scripts/build_bus.py` after the identity graph rebuild). On
the first refresh of each calendar month the full leaderboards are frozen into
`bus_benchmark_snapshots` keyed by (month, kind, slug) with insert-only semantics. The series is
served by `GET /api/buses/history/{slug}` with the methodology version that produced each row.

The precise guarantee, as of v1.4: **a month is captured in full by the first refresh of that
calendar month and never written again**, including for cohorts that first appear mid-month,
which simply wait for the next month's capture. Earlier behavior was weaker (insert-only per
key, so a cohort first seen mid-month appended itself into an already-frozen month with that
day's values, observable in the July 2026 capture: 2,650 rows on the 23rd and 3 more on the
27th). The July 2026 month retains those three appended rows as a documented artifact of the
old rule; from August 2026 onward each month is written exactly once.

## 8. Provenance statement

Every published number is traceable to source rows:

* Each satellite's attribution row carries its source (`gcat`), the source-native row key
  (`source_key` = GCAT jcat id) and the `ingest_run_id` of the snapshot it came from, and the
  raw bus and manufacturer claims are additionally recorded in `source_assertion` following the
  identity layer's provenance pattern.
* Each rolled-up manufacturer records its resolution path (`rollup_path`) and whether a curated
  override participated (`rollup_source`).
* For any headline metric, `GET /api/buses/{slug}/provenance?metric=...` returns the exact
  constituent satellites with their per-satellite values and attribution provenance; the UI
  exposes this as the Receipts panel. The receipts total equals the headline number it backs.
* Behavior values derive from `sat_daily`, which is an aggregate of `gp_elements` rows each
  carrying their own ingest ledger entry.

## 9. Corrections and disputes

Operators and manufacturers can confirm or dispute attribution: email vibhavgupta2@gmail.com
with subject "Bus attribution: {name}". Adjudication rule: a correction accepted after review
is recorded as a `source_assertion` with source `operator_confirmed`, which outranks catalog
sources in the resolution precedence, so the correction enters the record with provenance
rather than overwriting it. The original catalog claim remains visible in the assertion
history.

## Changelog

* **v1.5 (2026-07-31).** Join provenance published per satellite: the resolver now joins by
  permanent NORAD id first, then by COSPAR crosswalk to anchored satellites, then records
  remaining rows as provisional slot occupancy, with the rule and a churn flag exposed on
  every row and `provisional_n` on every cohort. An opt-in `state=anchored` filter serves
  anchored-only aggregations; the default keeps provisional fleets visible and flagged. The
  NORAD-first rule also corrected seven attributions the crosswalk had been losing to
  duplicate COSPAR designations: six Starshield satellites (SpaceX moves from 12,829 to
  12,835) and one HawkEye-family satellite (Argotec moves from 14 to 15). This delivers the
  confirmed-versus-provisional split recorded as tracked work in v1.3. No metric definitions
  changed.

* **v1.4 (2026-07-29).** Attribution rule addition: an operator-identity merge layer now joins
  manufacturer cohorts whose rolled-up GCAT group codes resolve to the same organization in the
  curated operator graph. The layer is merge-only and keyed on group codes, so it cannot split a
  cohort. Its one effect on current data is the Planet family: `plabs` (Planet Labs, 106),
  `cosmog` (2) and `skybox` (2) merge into `plan`, whose fleet moves from 551 to 661; the three
  retired slugs redirect permanently via `benchmark_slug_alias` and their frozen series name
  `plan` as their continuation. No other cohort changed on any metric, verified by a controlled
  before-and-after diff on identical data. Also in this version: both leaderboard views now group
  by slug alone so one URL can never serve two cohorts (the defect behind the July `raym`
  archive row, which records the phantom `RAYM?` cohort from before the v1.2 fix rather than the
  real Raymond EL); and monthly snapshots are now written exactly once per month rather than
  insert-only per key. Metric definitions are unchanged.
* **v1.3 (2026-07-28).** Two disclosure corrections found by auditing this document against the
  running system, no metric definitions changed. Section 7 previously said a captured month is
  never added to, which is not what the code does: insert-only is enforced per key, so a cohort
  first seen mid-month is still inserted into that month. Section 6 now records that manufacturer
  and bus-model slugs share one URL namespace, that manufacturers win a collision, and that the
  shadowed cohort is advertised in the API response so no cohort is unreachable.
* **v1.2 (2026-07-28).** Two corrections, no metric definitions changed. First, GCAT's `?`
  uncertainty marker is now stripped per code rather than from the end of the whole string,
  because on a compound value GCAT marks the individual org. The previous rule left `RAYM?` as a
  resolved code, which matched no organization and then slugified onto the real `RAYM`, so one
  published URL served two different manufacturers and the frozen monthly archive could capture
  only one of them. Raymond EL moves from 3 to 4 satellites and the spurious cohort disappears.
  Second, this document previously stated that GCAT lists the prime contractor first on a
  co-manufactured object. That was our assumption presented as a catalog semantic, and it is not
  supported: GCAT does not document the slash, and 8 org pairs appear in both orders in the same
  snapshot. The first-listed convention still governs attribution, but it is now labeled as ours
  and its cost is quantified.
* **v1.1 (2026-07-27).** Attribution rule change: satellites now resolve by COSPAR piece
  designation first, with the GCAT catalog id as fallback. Found via the Apex Space fleet,
  where catalog-id matching dropped one Apex satellite from the fleet count and joined three
  others to the wrong canonical records. The justifying event is GCAT's 2026-07-10 release,
  which renumbered 67 of the 73 Transporter-17 catalog ids against pieces that held. A later
  release, 2026-07-27, moved no keys at all and instead re-identified the occupants of 41 of
  those 73 slots, which piece-first matching does not protect against and which no key-based
  rule can. The counting convention and the provisional-identification caveat in section 3
  were added in the same revision. No metric definitions changed.
* **v1.0 (2026-07-23).** Initial published methodology: GCAT-based attribution with
  business-class parent rollup and the single SPXS to SPX override; behavior metrics reusing
  SPEC 7.1/7.2/7.3 definitions with the 0.100 km station-keeping threshold; cohort floor
  n >= 5; monthly immutable snapshots; per-metric provenance endpoint; correction channel with
  `operator_confirmed` precedence.
