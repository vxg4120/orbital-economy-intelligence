"""GET /api/stats -- the Overview dashboard payload.

Headline counts, coverage percentages (mirroring quality/report.py section 6), the three conflict
counts, and the ingestion ledger (last run per source/endpoint/status, matching report.py's
section header). All read-only aggregates.
"""

from fastapi import APIRouter, Depends

from api.deps import get_db
from api.routers.conflicts import (
    count_decay_conflicts,
    count_stale_owners,
    count_status_conflicts,
)

router = APIRouter(tags=["stats"])


# On-orbit payloads + coverage denominators. Verbatim semantics from quality/report.py
# _section_coverage: on-orbit = PAYLOAD whose latest canonical status != DECAYED.
_COVERAGE_SQL = """
WITH latest_status AS (
    SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
    FROM satellite_status_history
    ORDER BY satellite_id, observed_at DESC
),
on_orbit AS (
    SELECT s.satellite_id
    FROM satellite s
    LEFT JOIN latest_status ls ON ls.satellite_id = s.satellite_id
    WHERE s.object_type = 'PAYLOAD'
      AND COALESCE(ls.canonical_status, 'UNKNOWN') != 'DECAYED'
),
with_operator AS (
    SELECT DISTINCT satellite_id FROM satellite_operator
    WHERE role = 'owner' AND valid_to IS NULL
),
with_status AS (
    SELECT satellite_id FROM latest_status WHERE canonical_status != 'UNKNOWN'
),
id_counts AS (
    SELECT satellite_id, count(*) AS n_ids FROM satellite_identifier GROUP BY satellite_id
)
SELECT
    (SELECT count(*) FROM on_orbit) AS total_on_orbit,
    (SELECT count(*) FROM on_orbit oo JOIN with_operator wo
        ON wo.satellite_id = oo.satellite_id) AS with_operator,
    (SELECT count(*) FROM on_orbit oo JOIN with_status ws
        ON ws.satellite_id = oo.satellite_id) AS with_status,
    (SELECT count(*) FROM on_orbit oo JOIN id_counts ic
        ON ic.satellite_id = oo.satellite_id AND ic.n_ids >= 2) AS with_2plus_ids
"""

# Last run per (source, endpoint, status), matching quality/report.py _section_header.
_INGEST_SQL = """
SELECT source, endpoint, status, finished_at, rows_ingested
FROM (
    SELECT DISTINCT ON (source, endpoint, status)
        source, endpoint, status, finished_at, rows_ingested
    FROM ingest_run
    ORDER BY source, endpoint, status, finished_at DESC NULLS LAST
) last_per_endpoint_status
ORDER BY source, endpoint, status
"""


def _pct(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round(100.0 * numerator / denominator, 1)


@router.get("/stats")
def get_stats(db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM satellite) AS satellites,
                (SELECT count(*) FROM operator) AS operators,
                (SELECT count(*) FROM satellite_identifier) AS identifier_rows,
                (SELECT count(*) FROM merge_log) AS merge_events,
                (SELECT count(*) FROM gp_elements) AS gp_elements
            """
        )
        totals = cur.fetchone()

        cur.execute(_COVERAGE_SQL)
        cov = cur.fetchone()

        cur.execute(_INGEST_SQL)
        ingest_runs = cur.fetchall()

    total = cov["total_on_orbit"]
    return {
        "satellites": totals["satellites"],
        "on_orbit_payloads": total,
        "operators": totals["operators"],
        "identifier_rows": totals["identifier_rows"],
        "merge_events": totals["merge_events"],
        "gp_elements": totals["gp_elements"],
        "coverage": {
            "operator_pct": _pct(cov["with_operator"], total),
            "status_pct": _pct(cov["with_status"], total),
            "multi_source_pct": _pct(cov["with_2plus_ids"], total),
        },
        "conflicts": {
            "status": count_status_conflicts(db),
            "decay": count_decay_conflicts(db),
            "stale_owners": count_stale_owners(db),
        },
        "ingest_runs": ingest_runs,
    }
