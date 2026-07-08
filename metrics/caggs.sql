-- Continuous aggregate: daily per-satellite orbital stats. SPEC.md §6, transcribed verbatim.
--
-- Keyed by norad_id ONLY -- continuous aggregates cannot join across tables, so operator
-- attribution happens above this, in metrics/benchmark_views.sql (v_sat_operator_daily), via a
-- temporal range-join to satellite_operator. That split is deliberate: identity churn (an
-- acquisition, a re-flagged owner code) must never invalidate the underlying physics aggregate.
--
-- Idempotency note: tested directly against an empty gp_elements hypertable (no chunks yet) and
-- CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous) succeeds without WITH NO DATA --
-- TimescaleDB 2.28 materializes an (empty) result set immediately.
--
-- materialized_only = false (real-time aggregation) is set explicitly: TimescaleDB 2.28's
-- default for a freshly-created cagg is materialized_only = TRUE, which means sat_daily would
-- return nothing for data landed since the last refresh (an hourly job here). We need queries
-- against sat_daily to reflect just-ingested GP rows immediately -- both for the demo layer
-- feeling live and so tests don't have to orchestrate a refresh -- so real-time aggregation is
-- switched on explicitly rather than relying on a default that no longer matches the docs.
CREATE MATERIALIZED VIEW IF NOT EXISTS sat_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT norad_id,
       time_bucket('1 day', epoch) AS day,
       avg(semi_major_axis_km)      AS sma_avg,
       stddev_samp(semi_major_axis_km) AS sma_stddev,
       min(perigee_km)              AS perigee_min,
       max(apogee_km)               AS apogee_max,
       count(*)                     AS elset_count
FROM gp_elements
GROUP BY 1, 2;

-- Refresh policy: keep the materialized window current on a 1h schedule (GP data itself only
-- updates every 2h per CelesTrak's politeness rule, so 1h is plenty responsive). start_offset of
-- 7 days covers the rolling 30-day station-keeping window's tail with margin without forcing a
-- full-history re-materialization on every run; end_offset of 1h avoids racing the still-filling
-- current bucket. if_not_exists => TRUE so re-running this file is a no-op.
SELECT add_continuous_aggregate_policy('sat_daily',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);
