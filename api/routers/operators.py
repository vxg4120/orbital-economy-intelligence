"""Operator league table and per-operator detail (the Operators view's data).

Fleet counts are over the CURRENT owned fleet (role='owner', valid_to IS NULL) joined to each
satellite's latest canonical status: on-orbit = status != DECAYED, active = status ACTIVE.
The detail endpoint adds the MSO hierarchy (parents/children/acquisitions from
operator_relationship) and a fleet-by-orbital-regime breakdown from the latest element set per
owned satellite.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db

router = APIRouter(prefix="/operators", tags=["operators"])

_SORTS = {"fleet": "fleet_total DESC, operator_id", "name": "canonical_name, operator_id"}

# Current owned fleet per operator, joined to latest status; windowed total for pagination.
_LEAGUE_SQL = """
WITH latest_status AS (
    SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
    FROM satellite_status_history
    ORDER BY satellite_id, observed_at DESC
),
owned AS (
    SELECT DISTINCT operator_id, satellite_id
    FROM satellite_operator WHERE role = 'owner' AND valid_to IS NULL
),
agg AS (
    SELECT
        o.operator_id, o.canonical_name, o.country, o.operator_class,
        count(ow.satellite_id) AS fleet_total,
        count(*) FILTER (
            WHERE COALESCE(ls.canonical_status, 'UNKNOWN') <> 'DECAYED') AS fleet_on_orbit,
        count(*) FILTER (WHERE ls.canonical_status = 'ACTIVE') AS fleet_active
    FROM operator o
    JOIN owned ow ON ow.operator_id = o.operator_id
    LEFT JOIN latest_status ls ON ls.satellite_id = ow.satellite_id
    GROUP BY o.operator_id
)
SELECT
    agg.*,
    (SELECT p.canonical_name
       FROM operator_relationship orl JOIN operator p ON p.operator_id = orl.parent_id
       WHERE orl.child_id = agg.operator_id
         AND orl.valid_from <= current_date
         AND (orl.valid_to IS NULL OR orl.valid_to > current_date)
       ORDER BY orl.valid_from DESC LIMIT 1) AS parent_name,
    count(*) OVER() AS total
FROM agg
ORDER BY {order}
LIMIT %(limit)s OFFSET %(offset)s
"""


@router.get("")
def league_table(
    db=Depends(get_db),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("fleet"),
):
    order = _SORTS.get(sort)
    if order is None:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(_SORTS)}")
    with db.cursor() as cur:
        cur.execute(_LEAGUE_SQL.format(order=order), {"limit": limit, "offset": offset})
        rows = cur.fetchall()
    total = rows[0]["total"] if rows else 0
    for r in rows:
        r.pop("total", None)
    return {"rows": rows, "total": total}


@router.get("/{operator_id}")
def detail(operator_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute(
            "SELECT operator_id, canonical_name, country, operator_class "
            "FROM operator WHERE operator_id = %s",
            (operator_id,),
        )
        operator = cur.fetchone()
        if operator is None:
            raise HTTPException(status_code=404, detail="operator not found")

        cur.execute(
            "SELECT orl.parent_id AS operator_id, p.canonical_name, orl.relationship, "
            "       orl.valid_from, orl.valid_to "
            "FROM operator_relationship orl JOIN operator p ON p.operator_id = orl.parent_id "
            "WHERE orl.child_id = %s ORDER BY orl.valid_from, orl.relationship",
            (operator_id,),
        )
        parents = cur.fetchall()

        cur.execute(
            "SELECT orl.child_id AS operator_id, c.canonical_name, orl.relationship, "
            "       orl.valid_from, orl.valid_to "
            "FROM operator_relationship orl JOIN operator c ON c.operator_id = orl.child_id "
            "WHERE orl.parent_id = %s ORDER BY orl.valid_from, orl.relationship",
            (operator_id,),
        )
        children = cur.fetchall()

        # Acquisition history: M&A edges either direction, counterpart named as child/parent.
        cur.execute(
            "SELECT c.canonical_name AS child, p.canonical_name AS parent, orl.relationship, "
            "       orl.valid_from, orl.valid_to "
            "FROM operator_relationship orl "
            "JOIN operator c ON c.operator_id = orl.child_id "
            "JOIN operator p ON p.operator_id = orl.parent_id "
            "WHERE orl.relationship IN ('acquired_by', 'merged_into') "
            "  AND (orl.child_id = %s OR orl.parent_id = %s) "
            "ORDER BY orl.valid_from",
            (operator_id, operator_id),
        )
        acquisitions = cur.fetchall()

        # Fleet by status over the current owned fleet.
        cur.execute(
            "WITH owned AS (SELECT DISTINCT satellite_id FROM satellite_operator "
            "               WHERE operator_id = %s AND role = 'owner' AND valid_to IS NULL), "
            "ls AS (SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status "
            "       FROM satellite_status_history WHERE satellite_id IN (SELECT satellite_id "
            "       FROM owned) ORDER BY satellite_id, observed_at DESC) "
            "SELECT COALESCE(ls.canonical_status, 'UNKNOWN') AS status, count(*) AS n "
            "FROM owned o LEFT JOIN ls ON ls.satellite_id = o.satellite_id "
            "GROUP BY 1 ORDER BY 2 DESC",
            (operator_id,),
        )
        fleet_by_status = {r["status"]: r["n"] for r in cur.fetchall()}

        # Fleet by orbital regime from the latest element set per owned satellite (mean altitude).
        cur.execute(
            "WITH owned AS (SELECT DISTINCT s.norad_id FROM satellite_operator so "
            "               JOIN satellite s ON s.satellite_id = so.satellite_id "
            "               WHERE so.operator_id = %s AND so.role = 'owner' "
            "                 AND so.valid_to IS NULL AND s.norad_id IS NOT NULL), "
            "le AS (SELECT DISTINCT ON (norad_id) norad_id, (perigee_km + apogee_km) / 2.0 AS alt "
            "       FROM gp_elements WHERE norad_id IN (SELECT norad_id FROM owned) "
            "       ORDER BY norad_id, epoch DESC) "
            "SELECT CASE WHEN alt < 2000 THEN 'LEO' WHEN alt < 35586 THEN 'MEO' "
            "            WHEN alt <= 35986 THEN 'GEO' ELSE 'HEO' END AS regime, count(*) AS n "
            "FROM le WHERE alt IS NOT NULL GROUP BY 1",
            (operator_id,),
        )
        fleet_by_regime = {r["regime"]: r["n"] for r in cur.fetchall()}

        cur.execute(
            "WITH owned AS (SELECT DISTINCT s.satellite_id, s.norad_id, s.cospar_id, "
            "                      s.canonical_name, s.object_type "
            "               FROM satellite_operator so JOIN satellite s "
            "                 ON s.satellite_id = so.satellite_id "
            "               WHERE so.operator_id = %s AND so.role = 'owner' "
            "                 AND so.valid_to IS NULL), "
            "ls AS (SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status "
            "       FROM satellite_status_history WHERE satellite_id IN (SELECT satellite_id "
            "       FROM owned) ORDER BY satellite_id, observed_at DESC, source) "
            "SELECT o.satellite_id, o.norad_id, o.cospar_id, o.canonical_name, o.object_type, "
            "       ls.canonical_status "
            "FROM owned o LEFT JOIN ls ON ls.satellite_id = o.satellite_id "
            "ORDER BY (ls.canonical_status = 'ACTIVE') DESC NULLS LAST, o.norad_id NULLS LAST "
            "LIMIT 20",
            (operator_id,),
        )
        top_satellites = cur.fetchall()

    fleet_total = sum(fleet_by_status.values())
    operator["fleet_total"] = fleet_total
    operator["fleet_on_orbit"] = sum(
        n for st, n in fleet_by_status.items() if st != "DECAYED")
    operator["fleet_active"] = fleet_by_status.get("ACTIVE", 0)

    # Owned satellites with no element set fall outside the four regimes; surface the remainder.
    classified = sum(fleet_by_regime.values())
    if fleet_total > classified:
        fleet_by_regime["NO_ELEMENTS"] = fleet_total - classified

    return {
        "operator": operator,
        "parents": parents,
        "children": children,
        "fleet_by_status": fleet_by_status,
        "fleet_by_regime": fleet_by_regime,
        "acquisitions": acquisitions,
        "top_satellites": top_satellites,
    }
