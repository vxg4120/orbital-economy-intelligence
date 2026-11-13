"""GET /api/congestion -- LEO altitude x inclination density bins for the heatmap.

Latest element set per NORAD id (mv_latest_gp_element), mean altitude = (apogee + perigee) / 2,
LEO focus (alt < 2000 km), bucketed into 50 km x 5 deg bins. Bin cardinality is naturally
bounded (~200 bins). This is a density proxy, not conjunction data.

What it counts: objects that have not reentered. The view keeps every object's last element set
forever, so an object that decayed keeps a bin at the altitude where its decay ended; the audit of
2026-11-13 found 863 such objects, 835 of them below 300 km, which drew a bright band where nothing
stays up. A bin drops an object whose recorded decay date falls on or after the day of its latest
element set. An element set newer than the recorded decay is kept, because it means the decay
date is wrong (Yunhai-1 02 and one Starlink were still being tracked after theirs).

What it does not count: the element feed is mostly CelesTrak's GROUP=active list, so debris and
spent rocket bodies are almost entirely absent (26 of about 17,900 rows on 2026-11-13). The field
is the active satellites, not the whole catalog.
"""

from fastapi import APIRouter, Depends

from api import cache
from api.deps import get_db

router = APIRouter(tags=["congestion"])

_CONGESTION_SQL = """
SELECT
    (floor(((m.apogee_km + m.perigee_km) / 2.0) / 50.0) * 50)::int AS alt_bin_km,
    (floor(m.inclination / 5.0) * 5)::int AS inc_bin_deg,
    count(*) AS object_count
FROM mv_latest_gp_element m
WHERE m.inclination IS NOT NULL AND m.perigee_km IS NOT NULL AND m.apogee_km IS NOT NULL
  AND ((m.apogee_km + m.perigee_km) / 2.0) < 2000
  AND NOT EXISTS (
      SELECT 1 FROM satellite s
      WHERE s.norad_id = m.norad_id
        AND s.decay_date >= (m.epoch AT TIME ZONE 'UTC')::date
  )
GROUP BY 1, 2
ORDER BY 1, 2
"""


def _build_congestion(db) -> dict:
    """Compute the density field over mv_latest_gp_element (17k rows, milliseconds). The cache
    stays: the value is page-load-hot, and the warm-refresh machinery costs next to nothing."""
    with db.cursor() as cur:
        cur.execute(_CONGESTION_SQL)
        return {"bins": cur.fetchall()}


_cache = cache.register("congestion", _build_congestion)


@router.get("/congestion")
def congestion(db=Depends(get_db)):
    return _cache.get(db)
