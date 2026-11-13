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


# --- per-operator exposure (docs/specs/congestion-exposure.md) --------------------------------
# v_congestion_exposure (metrics/benchmark_views.sql) bins the same field by altitude (50 km) and
# inclination (5 deg) at every altitude, one row per (shell, operator). operator_bin_weight is the
# share of the operator's own fleet in that shell, so a weighted sum over its shells is a mean
# over its satellites: the number of other objects that share a satellite's shell, split into the
# operator's own, other operators' and objects with no owner on record.
_EXPOSURE_SQL = """
WITH v AS MATERIALIZED (
    SELECT altitude_bin_50km AS alt, inclination_bin_5deg AS inc, bin_object_count AS n_bin,
           operator_id, operator_name, operator_object_count_in_bin AS n_op,
           operator_fleet_size AS fleet, operator_bin_weight AS w,
           operator_exposure_contribution AS c, bin_density_share AS share
    FROM v_congestion_exposure
),
owned AS (SELECT * FROM v WHERE operator_id IS NOT NULL),
summary AS (
    SELECT operator_id,
           max(fleet)::int AS fleet_in_field,
           count(*)::int AS shells,
           sum(w * (n_bin - 1))::float8 AS neighbours_per_satellite,
           sum(w * (n_op - 1))::float8 AS own_per_satellite,
           sum(w * (n_bin - n_op))::float8 AS others_per_satellite,
           sum(c)::float8 AS exposure_score
    FROM owned
    GROUP BY operator_id
),
busiest AS (
    SELECT DISTINCT ON (operator_id) operator_id, alt, inc, n_op::int AS operator_objects,
           n_bin::int AS objects
    FROM owned
    ORDER BY operator_id, n_op DESC, n_bin DESC, alt, inc
),
pairs AS (
    -- For each operator, how many of another operator's objects share a satellite's shell.
    SELECT x.operator_id, y.operator_id AS neighbour_id, y.operator_name AS neighbour_name,
           sum(x.w * y.n_op)::float8 AS per_satellite
    FROM owned x
    JOIN owned y ON y.alt = x.alt AND y.inc = x.inc AND y.operator_id <> x.operator_id
    GROUP BY 1, 2, 3
),
ranked AS (
    SELECT *, row_number() OVER (
               PARTITION BY operator_id ORDER BY per_satellite DESC, neighbour_id) AS rn
    FROM pairs
),
neighbours AS (
    -- One grouped pass; correlated subqueries per operator rescanned every pair 712 times and
    -- took 17.6 s on production on 2026-11-13.
    SELECT operator_id,
           sum(per_satellite)::float8 AS named_others,
           jsonb_agg(jsonb_build_object(
               'operator_id', neighbour_id, 'canonical_name', neighbour_name,
               'per_satellite', per_satellite) ORDER BY rn) FILTER (WHERE rn <= 3)
               AS top_neighbours
    FROM ranked
    GROUP BY operator_id
),
field AS (
    -- The field's size is the view's denominator (bin_density_share = n_bin / field). Summing
    -- the shells' counts over view rows would miss a shell holding only objects with no
    -- satellite row, since the view has no row for them.
    SELECT round(max(n_bin / share))::int AS field_objects FROM v
)
SELECT s.*, b.alt, b.inc, b.operator_objects, b.objects,
       COALESCE(n.named_others, 0)::float8 AS named_others,
       COALESCE(n.top_neighbours, '[]'::jsonb) AS top_neighbours,
       f.field_objects
FROM summary s
JOIN busiest b USING (operator_id)
LEFT JOIN neighbours n USING (operator_id)
CROSS JOIN field f
"""


def _build_operator_exposure(db) -> dict[int, dict]:
    """Every operator's exposure block, keyed by operator_id (about 1,600 view rows, so the
    self-join per shell stays small). An operator absent from the result has no object in the
    field."""
    with db.cursor() as cur:
        cur.execute(_EXPOSURE_SQL)
        rows = cur.fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        others = r["others_per_satellite"]
        out[r["operator_id"]] = {
            "fleet_in_field": r["fleet_in_field"],
            "shells": r["shells"],
            "neighbours_per_satellite": r["neighbours_per_satellite"],
            "own_per_satellite": r["own_per_satellite"],
            "others_per_satellite": others,
            # Objects in the field with no satellite row or no current owner. The subtraction
            # can leave float dust around zero, which is not a count.
            "unattributed_per_satellite": max(0.0, others - r["named_others"]),
            "top_neighbours": r["top_neighbours"],
            "busiest_shell": {
                "alt_km_from": r["alt"] * 50,
                "alt_km_to": r["alt"] * 50 + 50,
                "inc_deg_from": r["inc"] * 5,
                "inc_deg_to": r["inc"] * 5 + 5,
                "operator_objects": r["operator_objects"],
                "objects": r["objects"],
            },
            "exposure_score": r["exposure_score"],
            "field_objects": r["field_objects"],
        }
    return out


_exposure_cache = cache.register("operator_exposure", _build_operator_exposure)


def operator_exposure(db, operator_id: int) -> dict | None:
    """One operator's exposure block, or None when none of its objects is in the field."""
    return _exposure_cache.get(db).get(operator_id)
