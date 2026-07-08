-- Operator benchmark views. SPEC.md §7 metrics, as far as computable pre-Phase-2-backfill.
-- All views are CREATE OR REPLACE so this file is safe to re-apply.

-- ---------------------------------------------------------------------------------------------
-- v_sat_operator_daily
-- ---------------------------------------------------------------------------------------------
-- Operator attribution happens ABOVE the sat_daily continuous aggregate, in this plain view, via
-- a temporal range-join to satellite_operator (role='owner', day::date BETWEEN valid_from AND
-- COALESCE(valid_to,'infinity')). This is deliberate: identity churn -- an acquisition, a
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
   AND sd.day::date BETWEEN so.valid_from AND COALESCE(so.valid_to, 'infinity'::date)
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

-- ---------------------------------------------------------------------------------------------
-- Time-to-operational (SPEC §7.2) -- Phase 2 placeholder.
-- ---------------------------------------------------------------------------------------------
-- Days from launch to orbit acquisition (sma settling within a band of the constellation shell's
-- median and holding for N consecutive days). Requires the orbit-raising curve, i.e. gp_history
-- backfill for each satellite's early post-launch element sets -- not available until Phase 2.
-- No view defined here; intentionally deferred.
