-- Operator benchmark views. SPEC.md §7 metrics + the §12 killer chart, computed over the Phase-2
-- gp_history backfill. All views are CREATE OR REPLACE so this file is safe to re-apply.

-- ---------------------------------------------------------------------------------------------
-- v_sat_operator_daily
-- ---------------------------------------------------------------------------------------------
-- Operator attribution happens ABOVE the sat_daily continuous aggregate, in this plain view, via
-- a temporal range-join to satellite_operator (role='owner'). The range is HALF-OPEN
-- [valid_from, valid_to): a day is attributed to the operator whose window starts on/before it and
-- ends strictly after it. This matches how identity/resolve.py writes SCD2 ownership -- an
-- acquisition split writes child=(launch, split] and parent=[split, NULL) as two adjacent rows
-- that SHARE the split boundary date, so a closed BETWEEN would match BOTH on the transition day
-- and double-count that satellite/day under both operators. Half-open attributes the boundary day
-- to exactly one operator: the incoming (parent) one whose window is [split, ...). This is
-- deliberate: identity churn -- an acquisition, a
-- re-flagged owner code -- must not invalidate the underlying physics aggregate in sat_daily.
-- A satellite's orbit does not change the day its owner changes; only which operator this view
-- attributes that orbit to changes. This is the mechanic behind the "killer chart": the same
-- sma_avg time series attributed to OneWeb before an acquisition date and to Eutelsat after it,
-- with no discontinuity in the underlying physics.
CREATE OR REPLACE VIEW v_sat_operator_daily AS
SELECT
    sd.norad_id,
    sd.day,
    sd.sma_avg,
    sd.sma_stddev,
    sd.perigee_min,
    sd.apogee_max,
    sd.elset_count,
    s.satellite_id,
    s.canonical_name,
    so.operator_id,
    o.canonical_name AS operator_name
FROM sat_daily sd
JOIN satellite s ON s.norad_id = sd.norad_id
LEFT JOIN satellite_operator so
    ON so.satellite_id = s.satellite_id
   AND so.role = 'owner'
   -- Half-open SCD2 window [valid_from, valid_to): boundary day belongs to the incoming operator.
   AND sd.day::date >= so.valid_from
   AND sd.day::date < COALESCE(so.valid_to, 'infinity'::date)
LEFT JOIN operator o ON o.operator_id = so.operator_id;

-- ---------------------------------------------------------------------------------------------
-- v_station_keeping_30d
-- ---------------------------------------------------------------------------------------------
-- SPEC §7.1: rolling 30-day stddev of daily sma_avg per satellite, then aggregated per operator
-- for currently-ACTIVE payloads (canonical status from the latest satellite_status_history row).
-- Lower rolling stddev = tighter station-keeping = a proxy for propulsion health / operational
-- tempo.
CREATE OR REPLACE VIEW v_station_keeping_30d AS
WITH per_satellite AS (
    SELECT
        satellite_id,
        canonical_name,
        operator_id,
        operator_name,
        day,
        stddev_samp(sma_avg) OVER (
            PARTITION BY satellite_id
            ORDER BY day
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND CURRENT ROW
        ) AS rolling_stddev_30d
    FROM v_sat_operator_daily
    WHERE satellite_id IS NOT NULL
),
latest_status AS (
    SELECT DISTINCT ON (satellite_id)
        satellite_id, canonical_status
    FROM satellite_status_history
    ORDER BY satellite_id, observed_at DESC
)
SELECT
    ps.satellite_id,
    ps.canonical_name,
    ps.operator_id,
    ps.operator_name,
    ps.day,
    ps.rolling_stddev_30d
FROM per_satellite ps
JOIN latest_status ls ON ls.satellite_id = ps.satellite_id
WHERE ls.canonical_status = 'ACTIVE';

-- Per-operator rollup of the above: average rolling 30-day station-keeping stddev across each
-- operator's currently-ACTIVE fleet, as of each satellite's latest data day.
CREATE OR REPLACE VIEW v_station_keeping_30d_by_operator AS
WITH latest_per_satellite AS (
    SELECT DISTINCT ON (satellite_id)
        satellite_id, operator_id, operator_name, rolling_stddev_30d
    FROM v_station_keeping_30d
    ORDER BY satellite_id, day DESC
)
SELECT
    operator_id,
    operator_name,
    count(*) AS active_satellite_count,
    avg(rolling_stddev_30d) AS avg_rolling_stddev_30d_km
FROM latest_per_satellite
WHERE operator_id IS NOT NULL
GROUP BY operator_id, operator_name;

-- ---------------------------------------------------------------------------------------------
-- v_congestion_exposure
-- ---------------------------------------------------------------------------------------------
-- SPEC §7.4: altitude (50 km) x inclination (5 deg) bins built from the LATEST element set per
-- object in gp_elements -- works with current GP data only, no history required. Grain is
-- (altitude_bin, inclination_bin, operator_id): each row carries both the bin's overall object
-- density (bin_object_count / bin_density_share, independent of operator) and this operator's
-- contribution to that bin. operator_exposure_contribution is fleet-weighted: an operator's
-- share of ITS OWN fleet that sits in this bin, times the bin's share of catalog-wide density.
-- Summed across bins per operator (SUM(operator_exposure_contribution) GROUP BY operator_id) it
-- yields a single per-operator congestion-exposure score. This is a density proxy, not real
-- conjunction/collision data (which is restricted) -- documented plainly in the README.
CREATE OR REPLACE VIEW v_congestion_exposure AS
WITH latest_elements AS (
    SELECT DISTINCT ON (norad_id)
        norad_id, perigee_km, apogee_km, inclination
    FROM gp_elements
    ORDER BY norad_id, epoch DESC
),
binned AS (
    SELECT
        norad_id,
        floor(((perigee_km + apogee_km) / 2.0) / 50.0)::int AS altitude_bin_50km,
        floor(inclination / 5.0)::int AS inclination_bin_5deg
    FROM latest_elements
    WHERE inclination IS NOT NULL AND perigee_km IS NOT NULL AND apogee_km IS NOT NULL
),
total_objects AS (
    SELECT count(*)::numeric AS n FROM binned
),
bin_density AS (
    SELECT altitude_bin_50km, inclination_bin_5deg, count(*) AS bin_object_count
    FROM binned
    GROUP BY altitude_bin_50km, inclination_bin_5deg
),
sat_operator AS (
    -- Current (open-ended) owner per satellite; satellites with no open owner row surface with
    -- operator_id NULL and fold into an "unresolved" bucket below.
    SELECT s.norad_id, so.operator_id, o.canonical_name AS operator_name
    FROM satellite s
    LEFT JOIN satellite_operator so
        ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
    LEFT JOIN operator o ON o.operator_id = so.operator_id
),
operator_fleet AS (
    SELECT so.operator_id, count(*)::numeric AS fleet_size
    FROM binned b
    JOIN sat_operator so ON so.norad_id = b.norad_id
    GROUP BY so.operator_id
)
SELECT
    b.altitude_bin_50km,
    b.inclination_bin_5deg,
    bd.bin_object_count,
    bd.bin_object_count::numeric / t.n AS bin_density_share,
    so.operator_id,
    so.operator_name,
    count(*) AS operator_object_count_in_bin,
    of.fleet_size AS operator_fleet_size,
    count(*)::numeric / of.fleet_size AS operator_bin_weight,
    (count(*)::numeric / of.fleet_size) * (bd.bin_object_count::numeric / t.n)
        AS operator_exposure_contribution
FROM binned b
JOIN bin_density bd USING (altitude_bin_50km, inclination_bin_5deg)
JOIN sat_operator so ON so.norad_id = b.norad_id
JOIN operator_fleet of ON of.operator_id IS NOT DISTINCT FROM so.operator_id
CROSS JOIN total_objects t
GROUP BY
    b.altitude_bin_50km, b.inclination_bin_5deg, bd.bin_object_count,
    so.operator_id, so.operator_name, of.fleet_size, t.n;

-- ---------------------------------------------------------------------------------------------
-- v_deorbit_compliance
-- ---------------------------------------------------------------------------------------------
-- SPEC §7.3 skeleton: for satellites whose latest canonical status is DECAYED or INACTIVE,
-- elapsed time from last-observed-ACTIVE (satellite_status_history) to satellite.decay_date,
-- compliant = elapsed <= 5 years (the FCC post-mission-disposal rule). This view will be SPARSE
-- until Phase 2 (gp_history / decay-message backfill fills in decay_date and status-history
-- depth for older objects) -- most rows showing NULL last_active_at or NULL decay_date is
-- expected here, not a bug.
CREATE OR REPLACE VIEW v_deorbit_compliance AS
WITH latest_status AS (
    SELECT DISTINCT ON (satellite_id)
        satellite_id, canonical_status
    FROM satellite_status_history
    ORDER BY satellite_id, observed_at DESC
),
last_active AS (
    SELECT satellite_id, max(observed_at) AS last_active_at
    FROM satellite_status_history
    WHERE canonical_status = 'ACTIVE'
    GROUP BY satellite_id
)
SELECT
    s.satellite_id,
    s.norad_id,
    s.canonical_name,
    ls.canonical_status,
    la.last_active_at,
    s.decay_date,
    (s.decay_date - la.last_active_at::date) AS elapsed_days,
    CASE
        WHEN s.decay_date IS NULL OR la.last_active_at IS NULL THEN NULL
        WHEN s.decay_date <= (la.last_active_at::date + INTERVAL '5 years') THEN TRUE
        ELSE FALSE
    END AS compliant
FROM satellite s
JOIN latest_status ls ON ls.satellite_id = s.satellite_id
LEFT JOIN last_active la ON la.satellite_id = s.satellite_id
WHERE ls.canonical_status IN ('DECAYED', 'INACTIVE');

-- ============================ v_time_to_operational (SPEC §7.2) ============================
CREATE OR REPLACE VIEW v_time_to_operational AS
WITH window_start AS (
    SELECT min(day)::date AS d FROM sat_daily
),
leo_daily AS (
    -- LEO payload daily orbit series, temporally attributed. LEO gate on sma_avg (semi-major axis,
    -- km from Earth centre): ~6478 km (100 km alt) .. 8378 km (2000 km alt). Payloads only, launched
    -- within the data window (launch_date on/after the earliest daily bucket).
    SELECT
        v.satellite_id, v.norad_id, v.operator_id, v.operator_name,
        s.launch_date, v.day::date AS d, v.sma_avg
    FROM v_sat_operator_daily v
    JOIN satellite s ON s.satellite_id = v.satellite_id
    CROSS JOIN window_start ws
    WHERE v.satellite_id IS NOT NULL
      AND v.operator_id IS NOT NULL
      AND s.object_type = 'PAYLOAD'
      AND s.launch_date IS NOT NULL
      AND s.launch_date >= ws.d
      AND v.sma_avg BETWEEN 6478 AND 8378
),
per_sat AS (
    -- One row per satellite: its operator (on its latest data day) and its "eventual stable sma"
    -- = median sma over its LAST 30 observed days (its settled/operational orbit, after any
    -- orbit-raising). Requires >=7 observed days so the settle estimate isn't a single blip.
    SELECT
        satellite_id, norad_id, operator_id, operator_name, launch_date,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY sma_avg)
            FILTER (WHERE d > max_d - 30) AS stable_sma,
        count(*) AS n_days
    FROM (
        SELECT ld.*, max(d) OVER (PARTITION BY satellite_id) AS max_d
        FROM leo_daily ld
    ) x
    GROUP BY satellite_id, norad_id, operator_id, operator_name, launch_date
    HAVING count(*) >= 7
),
shell AS (
    -- Constellation shell = operator x 50 km altitude bin of the satellite's eventual stable sma.
    -- Shell median sma is the target band centre. shell_n = members, surfaced so the rollup can
    -- discount 1-2 member shells (a lone member trivially sits at its own median).
    SELECT
        operator_id, floor(stable_sma / 50.0)::int AS alt_bin_50km,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY stable_sma) AS shell_median_sma,
        count(*) AS shell_n
    FROM per_sat
    GROUP BY operator_id, floor(stable_sma / 50.0)::int
),
flagged AS (
    -- Per satellite-day: is sma_avg within +/-15 km of its shell median? Gaps-and-islands key
    -- (d - row_number()) is constant across a run of consecutive calendar days that are all
    -- in-band; a missing day or an out-of-band day shifts the row_number and breaks the run.
    SELECT
        ld.satellite_id, ps.norad_id, ps.operator_id, ps.operator_name, ps.launch_date,
        sh.shell_n, ld.d,
        (ld.d - (row_number() OVER (PARTITION BY ld.satellite_id ORDER BY ld.d))::int) AS grp
    FROM leo_daily ld
    JOIN per_sat ps ON ps.satellite_id = ld.satellite_id
    JOIN shell sh ON sh.operator_id = ps.operator_id
                 AND sh.alt_bin_50km = floor(ps.stable_sma / 50.0)::int
    WHERE abs(ld.sma_avg - sh.shell_median_sma) <= 15
),
streaks AS (
    -- Each in-band island: its start day and length. A "7 consecutive in-band days" streak is an
    -- island of length >= 7; the satellite is deemed operational on the island's FIRST day.
    SELECT satellite_id, norad_id, operator_id, operator_name, launch_date, shell_n,
           min(d) AS streak_start, count(*) AS streak_len
    FROM flagged
    GROUP BY satellite_id, norad_id, operator_id, operator_name, launch_date, shell_n, grp
    HAVING count(*) >= 7
)
-- Earliest qualifying streak per satellite -> time-to-operational (days from launch). Only
-- satellites that actually acquire the shell band appear (non-converging birds are omitted, not
-- counted as 0). Guard streak_start >= launch_date so a pre-launch data artefact can't go negative.
SELECT DISTINCT ON (satellite_id)
    satellite_id, norad_id, operator_id, operator_name, launch_date,
    streak_start AS operational_date,
    (streak_start - launch_date) AS days_to_operational,
    shell_n
FROM streaks
WHERE streak_start >= launch_date
ORDER BY satellite_id, streak_start;

-- Per-operator rollup: median days-to-operational and n over converging in-window LEO payloads.
-- Restricted to shells with >= 3 members (a meaningful shell median). n is the converging count.
CREATE OR REPLACE VIEW v_time_to_operational_by_operator AS
SELECT
    operator_id, operator_name,
    count(*) AS n_satellites,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY days_to_operational) AS median_days_to_operational,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY days_to_operational) AS p90_days_to_operational
FROM v_time_to_operational
WHERE shell_n >= 3
GROUP BY operator_id, operator_name;

-- ======================= v_station_keeping_operator (SPEC §7.1 rollup) =======================
-- Per-operator station-keeping tightness over the REAL history: for each ACTIVE payload take the
-- median of its 30-day rolling sma stddev across all its days (its typical tightness), then report
-- the operator-level p50/p90/mean of those per-satellite medians. Percentiles over per-satellite
-- medians (not raw sat-days) so long-lived birds don't dominate. Lower = tighter station-keeping.
-- Sanity (live history): SpaceX p50 ~40 m with a multi-km p90 tail (birds mid orbit-raising),
-- Eutelsat/OneWeb p50 ~5 m (very stable ~1200 km shells), Planet Labs p50 ~0.7 km (drifting doves).
CREATE OR REPLACE VIEW v_station_keeping_operator AS
WITH per_sat AS (
    SELECT
        satellite_id, operator_id, operator_name,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY rolling_stddev_30d) AS sat_median_stddev_km
    FROM v_station_keeping_30d
    WHERE rolling_stddev_30d IS NOT NULL
    GROUP BY satellite_id, operator_id, operator_name
)
SELECT
    operator_id, operator_name,
    count(*) AS active_satellite_count,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY sat_median_stddev_km) AS p50_station_keeping_km,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY sat_median_stddev_km) AS p90_station_keeping_km,
    avg(sat_median_stddev_km) AS mean_station_keeping_km
FROM per_sat
WHERE operator_id IS NOT NULL
GROUP BY operator_id, operator_name;

-- ================================= v_killer_chart (SPEC §12) =================================
-- The Phase-2 acceptance chart: a per-operator metric that VISIBLY changes under temporal identity
-- resolution vs naive SATCAT owner codes. Grain: (month, operator). Two attributions side by side:
--   (a) temporal SCD2 -- v_sat_operator_daily, each sat-day owned by whoever held it that day;
--   (b) naive SATCAT owner code -- the latest SATCAT 'owner' assertion mapped through
--       operator_alias(source='satcat'), applied to ALL history.
-- Why this is the honest variant for THIS window: the only M&A boundary landing mid-window
-- (Intelsat->SES, 2025-07-17) is GEO -- those fleets were not backfilled, so they have zero
-- gp_history. The showcase the data DOES support is OneWeb->Eutelsat (merged 2023-09-28): SATCAT's
-- OWNER field is a country/agency code, not a company -- the 654-satellite ex-OneWeb LEO fleet is
-- coded 'UK' (which maps to no operator) and only Eutelsat's 54 legacy birds carry 'EUTE'. So naive
-- SATCAT owner codes attribute ~54 sats to Eutelsat while the identity graph's temporal resolution
-- attributes all ~708 -- and because the metric is computed over that fleet, sma-stability and
-- elset-days per month move with it. Live headline: Eutelsat ~260k temporal elset-days vs ~19k
-- naive (13.6x); the entire ex-OneWeb LEO fleet is invisible to naive SATCAT-owner attribution.
-- This is the identity graph's core value: resolving coarse country codes + M&A history into the
-- actual operating company.
CREATE OR REPLACE VIEW v_killer_chart AS
WITH temporal AS (
    SELECT
        date_trunc('month', day)::date AS month,
        operator_id, operator_name, norad_id, sma_stddev
    FROM v_sat_operator_daily
    WHERE operator_id IS NOT NULL
),
satcat_owner AS (
    SELECT DISTINCT ON (satellite_id) satellite_id, value AS owner_code
    FROM source_assertion
    WHERE attribute = 'owner' AND source = 'satcat' AND satellite_id IS NOT NULL
    ORDER BY satellite_id, observed_at DESC, ingest_run_id DESC, source_key
),
naive_map AS (
    -- norad -> operator strictly via the SATCAT owner code (country/agency string -> operator_alias)
    SELECT s.norad_id, oa.operator_id, o.canonical_name AS operator_name
    FROM satellite s
    JOIN satcat_owner so ON so.satellite_id = s.satellite_id
    JOIN operator_alias oa ON oa.source = 'satcat' AND lower(oa.alias) = lower(so.owner_code)
    JOIN operator o ON o.operator_id = oa.operator_id
),
naive AS (
    SELECT
        date_trunc('month', sd.day)::date AS month,
        nm.operator_id, nm.operator_name, sd.norad_id, sd.sma_stddev
    FROM sat_daily sd
    JOIN naive_map nm ON nm.norad_id = sd.norad_id
),
temporal_agg AS (
    SELECT month, operator_id, operator_name,
           count(DISTINCT norad_id) AS sats, count(*) AS sat_days, avg(sma_stddev) AS sma_stability
    FROM temporal GROUP BY month, operator_id, operator_name
),
naive_agg AS (
    SELECT month, operator_id, operator_name,
           count(DISTINCT norad_id) AS sats, count(*) AS sat_days, avg(sma_stddev) AS sma_stability
    FROM naive GROUP BY month, operator_id, operator_name
)
SELECT
    COALESCE(t.month, n.month) AS month,
    COALESCE(t.operator_id, n.operator_id) AS operator_id,
    COALESCE(t.operator_name, n.operator_name) AS operator_name,
    COALESCE(t.sats, 0) AS temporal_sats,
    COALESCE(t.sat_days, 0) AS temporal_sat_days,
    t.sma_stability AS temporal_sma_stability_km,
    COALESCE(n.sats, 0) AS naive_satcat_sats,
    COALESCE(n.sat_days, 0) AS naive_satcat_sat_days,
    n.sma_stability AS naive_satcat_sma_stability_km,
    COALESCE(t.sat_days, 0) - COALESCE(n.sat_days, 0) AS delta_sat_days,
    COALESCE(t.sats, 0) - COALESCE(n.sats, 0) AS delta_sats
FROM temporal_agg t
FULL OUTER JOIN naive_agg n USING (month, operator_id, operator_name);
