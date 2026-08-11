"""GET /api/congestion -- LEO altitude x inclination density bins for the heatmap.

Latest element set per NORAD id (DISTINCT ON ... ORDER BY norad_id, epoch DESC), mean altitude =
(apogee + perigee) / 2, LEO focus (alt < 2000 km), bucketed into 50 km x 5 deg bins. This is a
catalog-density proxy, not conjunction data. Bin cardinality is naturally bounded (~200 bins).
"""

from fastapi import APIRouter, Depends

from api import cache
from api.deps import get_db

router = APIRouter(tags=["congestion"])

_CONGESTION_SQL = """
WITH latest_elements AS (
    SELECT DISTINCT ON (norad_id) norad_id, perigee_km, apogee_km, inclination
    FROM gp_elements
    ORDER BY norad_id, epoch DESC
)
SELECT
    (floor(((apogee_km + perigee_km) / 2.0) / 50.0) * 50)::int AS alt_bin_km,
    (floor(inclination / 5.0) * 5)::int AS inc_bin_deg,
    count(*) AS object_count
FROM latest_elements
WHERE inclination IS NOT NULL AND perigee_km IS NOT NULL AND apogee_km IS NOT NULL
  AND ((apogee_km + perigee_km) / 2.0) < 2000
GROUP BY 1, 2
ORDER BY 1, 2
"""


def _build_congestion(db) -> dict:
    """Compute the density field. The DISTINCT ON reads all 10.2M element rows (~11.4 s warm),
    so it is served from a cache rather than recomputed per request."""
    with db.cursor() as cur:
        cur.execute(_CONGESTION_SQL)
        return {"bins": cur.fetchall()}


_cache = cache.register("congestion", _build_congestion)


@router.get("/congestion")
def congestion(db=Depends(get_db)):
    return _cache.get(db)
