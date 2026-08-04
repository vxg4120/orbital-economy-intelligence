-- Drag environment: what the thermosphere did to the LEO fleet, one row per day.
--
-- Lives in metrics/ (not a migration) because it reads the sat_daily continuous aggregate,
-- which scripts/apply_metrics.py creates; migrations must not depend on metrics objects.
--
-- mv_drag_daily measures the fleet-wide daily change in semi-major axis over LEO payload
-- observations (sma below 8378 km, i.e. altitude under ~2000 km), using only
-- consecutive-day pairs for the same object so multi-day gaps never dilute a per-day rate.
-- The median is the signal: it is robust to the positive tail of orbit-raising maneuvers
-- (a storm drags everyone down a little; propulsion lifts a few satellites a lot), and a
-- 10 km/day clamp discards element-set glitches entirely. Days with under 500 measured
-- pairs are withheld (partial ingest days and coverage boundaries produce wild medians from
-- tiny samples), and the current UTC day is always incomplete, so consumers should treat
-- the newest published row as provisional until the day closes.
--
-- MATERIALIZED because the pair-building window function scans every sat_daily row (~4M and
-- growing) before any day filter can apply -- 8 seconds nobody should pay on a page load.
-- The output is a few hundred rows. scripts/refresh_drag.py refreshes it (nightly, after GP
-- ingest, before the DQ report); like mv_bus_behavior_sat, a definition change here needs a
-- manual DROP MATERIALIZED VIEW because IF NOT EXISTS skips existing objects.

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_drag_daily AS
WITH pairs AS (
    SELECT norad_id, day, sma_avg,
           lag(sma_avg) OVER (PARTITION BY norad_id ORDER BY day) AS prev_sma,
           lag(day)     OVER (PARTITION BY norad_id ORDER BY day) AS prev_day
    FROM sat_daily
    WHERE sma_avg IS NOT NULL AND sma_avg < 8378
)
SELECT
    day::date                                                       AS day,
    count(*)                                                        AS sats_observed,
    round((percentile_cont(0.5) WITHIN GROUP
        (ORDER BY (sma_avg - prev_sma) * 1000.0))::numeric, 1)      AS median_dsma_m,
    round((percentile_cont(0.1) WITHIN GROUP
        (ORDER BY (sma_avg - prev_sma) * 1000.0))::numeric, 1)      AS p10_dsma_m,
    round((percentile_cont(0.9) WITHIN GROUP
        (ORDER BY (sma_avg - prev_sma) * 1000.0))::numeric, 1)      AS p90_dsma_m
FROM pairs
WHERE prev_day = day - interval '1 day'
  AND abs(sma_avg - prev_sma) < 10
GROUP BY 1
HAVING count(*) >= 500;

-- Unique index: lets REFRESH MATERIALIZED VIEW CONCURRENTLY keep the API readable mid-refresh.
CREATE UNIQUE INDEX IF NOT EXISTS mv_drag_daily_day ON mv_drag_daily (day);

-- Stable public name over the materialization, so consumers never bind to the mv directly.
CREATE OR REPLACE VIEW v_drag_daily AS
SELECT day, sats_observed, median_dsma_m, p10_dsma_m, p90_dsma_m FROM mv_drag_daily;

-- The joined surface the API and the DQ report read: indices FULL JOIN drag, so quiet-era
-- index days (back to 1957) and predicted forward days appear without drag numbers, and any
-- drag day missing an index row (should not happen; the SW file has no gaps) still shows.
CREATE OR REPLACE VIEW v_drag_environment_daily AS
SELECT
    COALESCE(sw.day, d.day)  AS day,
    sw.kp_max,
    sw.kp_sum,
    sw.ap_avg,
    sw.ap_max,
    sw.f107_obs,
    sw.f107_obs_center81,
    sw.f107_data_type,
    sw.storm_level,
    d.sats_observed,
    d.median_dsma_m,
    d.p10_dsma_m,
    d.p90_dsma_m
FROM v_space_weather_daily sw
FULL JOIN v_drag_daily d ON d.day = sw.day;
