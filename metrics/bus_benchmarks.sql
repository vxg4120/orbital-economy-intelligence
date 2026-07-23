-- Bus Benchmarks: per-manufacturer and per-bus-model performance views over the attribution in
-- satellite_bus (db/migrations/0009, built by scripts/build_bus.py). Metric definitions REUSE the
-- operator benchmark machinery in benchmark_views.sql rather than inventing new physics:
--   * station-keeping tightness  -> v_station_keeping_30d (SPEC 7.1 rolling 30-day sma stddev)
--   * time-to-operational        -> v_time_to_operational (SPEC 7.2 shell-acquisition streak)
--   * post-mission disposal      -> v_deorbit_compliance  (SPEC 7.3 five-year rule)
-- Full metric documentation: docs/BUS_BENCHMARKS_METHODOLOGY.md (versioned; served by
-- GET /api/buses/methodology).
--
-- Everything here is CREATE OR REPLACE (safe to re-apply), except mv_bus_behavior_sat which is a
-- plain materialized view (IF NOT EXISTS) refreshed by scripts/build_bus.py: the underlying
-- station-keeping / TTO views cost ~15s to scan, far too slow to recompute per API request.

-- ---------------------------------------------------------------------------------------------
-- mv_bus_behavior_sat
-- ---------------------------------------------------------------------------------------------
-- One row per satellite: its GP-behavior summary, precomputed. gp_days counts distinct observed
-- days in the sat_daily continuous aggregate (the honest "do we have behavior data" coverage
-- denominator, LEO-biased because that is what the GP backfill covers).
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_bus_behavior_sat AS
WITH gp AS (
    SELECT norad_id, count(*) AS gp_days,
           min(day)::date AS first_gp_day, max(day)::date AS last_gp_day
    FROM sat_daily
    GROUP BY norad_id
),
sk AS (
    -- Per-satellite typical station-keeping tightness: median of the 30-day rolling sma stddev,
    -- exactly as v_station_keeping_operator computes it (ACTIVE payloads only, by construction
    -- of v_station_keeping_30d).
    SELECT satellite_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY rolling_stddev_30d)
               AS sk_median_stddev_km
    FROM v_station_keeping_30d
    WHERE rolling_stddev_30d IS NOT NULL
    GROUP BY satellite_id
)
SELECT s.satellite_id, s.norad_id,
       COALESCE(gp.gp_days, 0) AS gp_days, gp.first_gp_day, gp.last_gp_day,
       sk.sk_median_stddev_km,
       tto.days_to_operational, tto.shell_n AS tto_shell_n,
       dc.compliant AS disposal_compliant
FROM satellite s
LEFT JOIN gp ON gp.norad_id = s.norad_id
LEFT JOIN sk ON sk.satellite_id = s.satellite_id
LEFT JOIN v_time_to_operational tto ON tto.satellite_id = s.satellite_id
LEFT JOIN v_deorbit_compliance dc ON dc.satellite_id = s.satellite_id;

CREATE UNIQUE INDEX IF NOT EXISTS mv_bus_behavior_sat_satellite_id
    ON mv_bus_behavior_sat (satellite_id);

-- ---------------------------------------------------------------------------------------------
-- v_bus_sat
-- ---------------------------------------------------------------------------------------------
-- The per-satellite grain both leaderboards aggregate: attribution x latest canonical status x
-- behavior summary. lifetime_days is only defined for DECAYED objects with both dates (the
-- decayed-cohort median lifetime; survivors are censored, not zero).
CREATE OR REPLACE VIEW v_bus_sat AS
WITH latest_status AS (
    SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
    FROM satellite_status_history
    ORDER BY satellite_id, observed_at DESC
)
SELECT
    sb.satellite_id, s.norad_id, s.cospar_id, s.canonical_name, s.object_type,
    s.launch_date, s.decay_date,
    COALESCE(ls.canonical_status, 'UNKNOWN') AS canonical_status,
    sb.bus_raw, sb.bus_model, sb.bus_slug, sb.bus_uncertain,
    sb.manufacturer_raw, sb.manufacturer_code, sb.manufacturer_codes, sb.manufacturer_uncertain,
    sb.manufacturer_org_name, sb.manufacturer_group_code, sb.manufacturer_name,
    sb.manufacturer_slug, sb.manufacturer_country, sb.rollup_source,
    sb.source, sb.source_key, sb.ingest_run_id,
    COALESCE(b.gp_days, 0) AS gp_days, b.first_gp_day, b.last_gp_day,
    b.sk_median_stddev_km, b.days_to_operational, b.tto_shell_n, b.disposal_compliant,
    CASE
        WHEN COALESCE(ls.canonical_status, 'UNKNOWN') = 'DECAYED'
             AND s.decay_date IS NOT NULL AND s.launch_date IS NOT NULL
             AND s.decay_date >= s.launch_date
        THEN (s.decay_date - s.launch_date)
    END AS lifetime_days
FROM satellite_bus sb
JOIN satellite s ON s.satellite_id = sb.satellite_id
LEFT JOIN latest_status ls ON ls.satellite_id = sb.satellite_id
LEFT JOIN mv_bus_behavior_sat b ON b.satellite_id = sb.satellite_id;

-- ---------------------------------------------------------------------------------------------
-- v_bus_benchmarks_manufacturer / v_bus_benchmarks_bus
-- ---------------------------------------------------------------------------------------------
-- The two leaderboards. Shared metric definitions (documented in the methodology doc):
--   fleet_total / fleet_on_orbit / fleet_active: cataloged payload counts by latest status,
--     on-orbit = status <> DECAYED (same rule as /api/operators).
--   decayed_share_pct, median_lifetime_years (lifetime_n): over the decayed cohort with dates.
--   median_days_to_operational (tto_n): median of v_time_to_operational per-satellite results,
--     restricted to shells with >= 3 members exactly like v_time_to_operational_by_operator.
--   station_keeping_share_pct (sk_n): share of behavior-observed ACTIVE payloads whose median
--     30-day rolling sma stddev is <= 0.100 km, i.e. the orbit is actively held rather than
--     drifting. p50_station_keeping_km is the cohort median of those per-satellite medians.
--   disposal_compliance_pct (disposal_n): share of v_deorbit_compliance rows with a decidable
--     verdict that met the 5-year rule. Sparse until decay-history backfill deepens.
--   gp_coverage_pct (gp_n): share of the fleet with ANY days in sat_daily. This is the honesty
--     meter: every behavior metric above is computed only over observed satellites, and this
--     column says how big that slice is (LEO-biased).
-- No cohort floor is baked in: cohort filtering (default n >= 5) happens in the API/MCP layer
-- so the floor stays configurable without redefining views.
CREATE OR REPLACE VIEW v_bus_benchmarks_manufacturer AS
SELECT
    manufacturer_slug, manufacturer_name, manufacturer_group_code, manufacturer_country,
    count(DISTINCT manufacturer_code) AS org_count,
    count(DISTINCT bus_slug) AS bus_model_count,
    count(*) AS fleet_total,
    count(*) FILTER (WHERE canonical_status <> 'DECAYED') AS fleet_on_orbit,
    count(*) FILTER (WHERE canonical_status = 'ACTIVE') AS fleet_active,
    count(*) FILTER (WHERE canonical_status = 'DECAYED') AS decayed_count,
    round(100.0 * count(*) FILTER (WHERE canonical_status = 'DECAYED') / count(*), 1)
        AS decayed_share_pct,
    count(lifetime_days) AS lifetime_n,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY lifetime_days / 365.25)
        FILTER (WHERE lifetime_days IS NOT NULL))::numeric, 2) AS median_lifetime_years,
    count(*) FILTER (WHERE days_to_operational IS NOT NULL AND tto_shell_n >= 3) AS tto_n,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY days_to_operational)
        FILTER (WHERE days_to_operational IS NOT NULL AND tto_shell_n >= 3)
        AS median_days_to_operational,
    count(sk_median_stddev_km) AS sk_n,
    round(100.0 * count(*) FILTER (WHERE sk_median_stddev_km <= 0.100)
        / NULLIF(count(sk_median_stddev_km), 0), 1) AS station_keeping_share_pct,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY sk_median_stddev_km)
        FILTER (WHERE sk_median_stddev_km IS NOT NULL))::numeric, 4) AS p50_station_keeping_km,
    count(disposal_compliant) AS disposal_n,
    round(100.0 * count(*) FILTER (WHERE disposal_compliant)
        / NULLIF(count(disposal_compliant), 0), 1) AS disposal_compliance_pct,
    count(*) FILTER (WHERE gp_days > 0) AS gp_n,
    round(100.0 * count(*) FILTER (WHERE gp_days > 0) / count(*), 1) AS gp_coverage_pct
FROM v_bus_sat
WHERE manufacturer_slug IS NOT NULL
GROUP BY manufacturer_slug, manufacturer_name, manufacturer_group_code, manufacturer_country;

CREATE OR REPLACE VIEW v_bus_benchmarks_bus AS
SELECT
    bus_slug, bus_model,
    mode() WITHIN GROUP (ORDER BY manufacturer_name) AS primary_manufacturer,
    mode() WITHIN GROUP (ORDER BY manufacturer_slug) AS primary_manufacturer_slug,
    count(DISTINCT manufacturer_slug) AS manufacturer_count,
    count(*) AS fleet_total,
    count(*) FILTER (WHERE canonical_status <> 'DECAYED') AS fleet_on_orbit,
    count(*) FILTER (WHERE canonical_status = 'ACTIVE') AS fleet_active,
    count(*) FILTER (WHERE canonical_status = 'DECAYED') AS decayed_count,
    round(100.0 * count(*) FILTER (WHERE canonical_status = 'DECAYED') / count(*), 1)
        AS decayed_share_pct,
    count(lifetime_days) AS lifetime_n,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY lifetime_days / 365.25)
        FILTER (WHERE lifetime_days IS NOT NULL))::numeric, 2) AS median_lifetime_years,
    count(*) FILTER (WHERE days_to_operational IS NOT NULL AND tto_shell_n >= 3) AS tto_n,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY days_to_operational)
        FILTER (WHERE days_to_operational IS NOT NULL AND tto_shell_n >= 3)
        AS median_days_to_operational,
    count(sk_median_stddev_km) AS sk_n,
    round(100.0 * count(*) FILTER (WHERE sk_median_stddev_km <= 0.100)
        / NULLIF(count(sk_median_stddev_km), 0), 1) AS station_keeping_share_pct,
    round((percentile_cont(0.5) WITHIN GROUP (ORDER BY sk_median_stddev_km)
        FILTER (WHERE sk_median_stddev_km IS NOT NULL))::numeric, 4) AS p50_station_keeping_km,
    count(disposal_compliant) AS disposal_n,
    round(100.0 * count(*) FILTER (WHERE disposal_compliant)
        / NULLIF(count(disposal_compliant), 0), 1) AS disposal_compliance_pct,
    count(*) FILTER (WHERE gp_days > 0) AS gp_n,
    round(100.0 * count(*) FILTER (WHERE gp_days > 0) / count(*), 1) AS gp_coverage_pct
FROM v_bus_sat
WHERE bus_slug IS NOT NULL
GROUP BY bus_slug, bus_model;
