"""Satellite search and the deep identity card (the Resolver view's data).

Search dispatches on the shape of ``q``: a bare number is an exact NORAD lookup, a COSPAR-shaped
token is an exact designator lookup, anything else is a name substring search (exact-prefix hits
first). The detail endpoint assembles the full provenance picture for one satellite: the identifier
crosswalk, SCD2 ownership timeline, status history, latest per-source assertions with the set of
conflicting attributes flagged, the latest orbit line, and the merge-audit footnote.

Resolved-status/current-operator semantics follow the deterministic ordering used across the
codebase (latest status by observed_at; current owner = role='owner', valid_to IS NULL).
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db

router = APIRouter(prefix="/satellites", tags=["satellites"])

_COSPAR_RE = re.compile(r"^\d{4}-\d{3}[A-Z]{1,3}$", re.IGNORECASE)

# Per-satellite resolved fields reused by search rows and the detail header. Correlated subqueries
# (bounded, one row each) keep NULL norad_id / missing owner / missing status graceful.
_RESOLVED_COLS = """
    s.satellite_id, s.norad_id, s.cospar_id, s.canonical_name, s.object_type,
    s.launch_date, s.decay_date,
    (SELECT o.canonical_name
       FROM satellite_operator so JOIN operator o ON o.operator_id = so.operator_id
       WHERE so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
       ORDER BY so.valid_from DESC LIMIT 1) AS operator_name,
    (SELECT ssh.canonical_status
       FROM satellite_status_history ssh
       WHERE ssh.satellite_id = s.satellite_id
       ORDER BY ssh.observed_at DESC, ssh.source LIMIT 1) AS canonical_status
"""


@router.get("/search")
def search(db=Depends(get_db), q: str = Query(..., min_length=1)):
    q = q.strip()
    if q.isdigit():
        where, params, order = "s.norad_id = %(norad)s", {"norad": int(q)}, "s.satellite_id"
    elif _COSPAR_RE.match(q):
        where, params, order = "upper(s.cospar_id) = upper(%(cospar)s)", {"cospar": q}, \
            "s.norad_id NULLS LAST, s.satellite_id"
    else:
        where = "s.canonical_name ILIKE %(contains)s"
        params = {"contains": f"%{q}%", "prefix": f"{q}%"}
        order = "(s.canonical_name ILIKE %(prefix)s) DESC, s.canonical_name, s.satellite_id"

    with db.cursor() as cur:
        cur.execute(
            f"SELECT {_RESOLVED_COLS} FROM satellite s WHERE {where} ORDER BY {order} LIMIT 20",
            params,
        )
        return {"results": cur.fetchall()}


@router.get("/{satellite_id}")
def detail(satellite_id: int, db=Depends(get_db)):
    with db.cursor() as cur:
        cur.execute(
            f"SELECT {_RESOLVED_COLS} FROM satellite s WHERE s.satellite_id = %s",
            (satellite_id,),
        )
        satellite = cur.fetchone()
        if satellite is None:
            raise HTTPException(status_code=404, detail="satellite not found")
        norad_id = satellite["norad_id"]

        cur.execute(
            "SELECT id_type, id_value, source, confidence, valid_from, valid_to "
            "FROM satellite_identifier WHERE satellite_id = %s "
            "ORDER BY id_type, source, id_value",
            (satellite_id,),
        )
        identifiers = cur.fetchall()

        cur.execute(
            "SELECT so.operator_id, o.canonical_name AS operator_name, so.role, "
            "       so.valid_from, so.valid_to, so.source, so.confidence "
            "FROM satellite_operator so JOIN operator o ON o.operator_id = so.operator_id "
            "WHERE so.satellite_id = %s "
            "ORDER BY so.role, so.valid_from, so.valid_to NULLS LAST",
            (satellite_id,),
        )
        ownership = cur.fetchall()

        cur.execute(
            "SELECT canonical_status, observed_at, source FROM satellite_status_history "
            "WHERE satellite_id = %s ORDER BY observed_at DESC, source",
            (satellite_id,),
        )
        status_history = cur.fetchall()

        # Latest assertion per (attribute, source), deterministic tiebreakers verbatim.
        cur.execute(
            "SELECT DISTINCT ON (attribute, source) attribute, value, source, observed_at "
            "FROM source_assertion WHERE satellite_id = %s "
            "ORDER BY attribute, source, observed_at DESC, ingest_run_id DESC, source_key",
            (satellite_id,),
        )
        assertions = cur.fetchall()

        latest_elements = None
        if norad_id is not None:
            cur.execute(
                "SELECT epoch, semi_major_axis_km, apogee_km, perigee_km, inclination, "
                "       eccentricity, mean_motion "
                "FROM gp_elements WHERE norad_id = %s ORDER BY epoch DESC LIMIT 1",
                (norad_id,),
            )
            latest_elements = cur.fetchone()

        cur.execute(
            "SELECT rule_fired, score, merged_at, details FROM merge_log "
            "WHERE surviving_id = %s OR merged_id = %s ORDER BY merged_at, merge_id LIMIT 50",
            (satellite_id, satellite_id),
        )
        merge_events = cur.fetchall()

    # An attribute conflicts when its latest per-source values disagree across sources.
    by_attr: dict[str, set] = {}
    for a in assertions:
        by_attr.setdefault(a["attribute"], set()).add(a["value"])
    conflicts = sorted(attr for attr, values in by_attr.items() if len(values) > 1)

    return {
        "satellite": satellite,
        "identifiers": identifiers,
        "ownership": ownership,
        "status_history": status_history,
        "assertions": assertions,
        "conflicts": conflicts,
        "latest_elements": latest_elements,
        "merge_events": merge_events,
    }
