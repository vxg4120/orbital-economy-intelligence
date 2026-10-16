-- The latest-status-per-satellite materialization.
--
-- Three metrics views (v_bus_sat, v_station_keeping_30d, v_deorbit_compliance) each re-derived
-- "the newest canonical_status per satellite" with a DISTINCT ON over the whole 3.1M-row
-- satellite_status_history table: measured at 3.1 s on the CX33 for 54k satellites, and the
-- buses leaderboard ran it twice per request (count + page), so GET /api/buses took 4 s on
-- every hit, warm or cold. Status history only moves when build_graph runs in the nightly, so
-- the answer is computed once here and refreshed by scripts/refresh_matviews.py right after
-- build_graph (the same slot as mv_latest_gp_element, migration 0019).
--
-- Ordering is the TOTAL order (observed_at DESC, then source) so equal timestamps resolve the
-- same way on every refresh; the live CTEs left that tie to the planner.
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_latest_satellite_status AS
SELECT DISTINCT ON (satellite_id)
    satellite_id,
    canonical_status,
    observed_at,
    source
FROM satellite_status_history
ORDER BY satellite_id, observed_at DESC, source;

-- Unique index: REFRESH MATERIALIZED VIEW CONCURRENTLY needs it, and satellite_id is the key.
CREATE UNIQUE INDEX IF NOT EXISTS mv_latest_satellite_status_sat
    ON mv_latest_satellite_status (satellite_id);
