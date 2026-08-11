-- The latest-element-per-satellite materialization.
--
-- Nine call sites (congestion, reachability's candidate sweep, audit, operators, four audit-
-- report sections, and metrics' v_congestion_exposure) each re-derived "the newest element set
-- per NORAD id" with a DISTINCT ON over the whole gp_elements hypertable: ~10.2M rows read to
-- yield ~17k, per query, per page load. The data only moves when GP ingest lands (twice daily),
-- so the answer is computed once here and refreshed by scripts/refresh_matviews.py right after
-- ingest in the nightly.
--
-- Ordering is the TOTAL order (epoch DESC, then source): the audit-report sites already
-- tiebroke equal epochs on source, while the API sites left ties to the planner. Every
-- consumer now inherits the same deterministic choice. Column list is the superset the nine
-- sites read; per-norad single-row lookups (satellites detail, pass prediction) stay on the
-- live table, where the (norad_id, epoch) access path is already cheap and fresher-than-
-- refresh semantics could matter.
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_latest_gp_element AS
SELECT DISTINCT ON (norad_id)
    norad_id,
    epoch,
    source,
    mean_motion,
    eccentricity,
    inclination,
    ra_of_asc_node,
    arg_of_pericenter,
    mean_anomaly,
    bstar,
    perigee_km,
    apogee_km
FROM gp_elements
ORDER BY norad_id, epoch DESC, source;

-- Unique index: REFRESH MATERIALIZED VIEW CONCURRENTLY needs it, and norad_id is the key.
CREATE UNIQUE INDEX IF NOT EXISTS mv_latest_gp_element_norad ON mv_latest_gp_element (norad_id);
