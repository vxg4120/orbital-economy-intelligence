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


# Downsample a long daily series into at most this many points (averaging consecutive rows into
# contiguous buckets) so the LifeTrack chart stays light no matter how many years a bird has flown.
_TRACK_CAP = 400


def _avg(values: list) -> float | None:
    nums = [v for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def _track_point(day, sma, perigee, apogee, elsets) -> dict:
    return {
        "day": day.isoformat() if day is not None else None,
        "sma_km": round(sma, 3) if sma is not None else None,
        "perigee_km": round(perigee, 3) if perigee is not None else None,
        "apogee_km": round(apogee, 3) if apogee is not None else None,
        "elsets": int(elsets) if elsets is not None else 0,
    }


def _bucket_track(rows: list[dict]) -> list[dict]:
    """Ordered daily rows -> LifeTrack points, downsampled to <= _TRACK_CAP by averaging.

    When the series fits the cap it passes through untouched; when it is longer, rows are split into
    _TRACK_CAP contiguous buckets whose numeric fields are averaged (elsets summed) and whose day is
    the bucket's midpoint — preserving the curve's shape (plateau then decay) at a bounded cost.
    """
    n = len(rows)
    if n == 0:
        return []
    if n <= _TRACK_CAP:
        return [
            _track_point(r["day"], r["sma_avg"], r["perigee_min"], r["apogee_max"], r["elset_count"])
            for r in rows
        ]
    points: list[dict] = []
    for i in range(_TRACK_CAP):
        chunk = rows[i * n // _TRACK_CAP:(i + 1) * n // _TRACK_CAP]
        if not chunk:
            continue
        mid = chunk[len(chunk) // 2]["day"]
        points.append(
            _track_point(
                mid,
                _avg([r["sma_avg"] for r in chunk]),
                _avg([r["perigee_min"] for r in chunk]),
                _avg([r["apogee_max"] for r in chunk]),
                sum(r["elset_count"] or 0 for r in chunk),
            )
        )
    return points


@router.get("/{satellite_id}/track")
def track(satellite_id: int, db=Depends(get_db)):
    """Daily orbit history (semi-major axis + perigee/apogee band) for one object, from ``sat_daily``.

    The series is keyed on the satellite's ``norad_id`` (always filter by NORAD first — sat_daily is
    multi-million-row) and ordered by day. A missing satellite is a 404; a NULL-norad or history-less
    object returns an empty series (``norad_id`` echoed, ``span_days`` 0) rather than an error, so the
    Resolver card and Review case screen render an honest "no element-set history" empty state.
    ``sma_km`` is the semi-major axis (radius from Earth centre); ``perigee_km``/``apogee_km`` are
    altitudes — the chart reconciles them by plotting sma as an altitude.
    """
    with db.cursor() as cur:
        cur.execute("SELECT norad_id FROM satellite WHERE satellite_id = %s", (satellite_id,))
        sat = cur.fetchone()
        if sat is None:
            raise HTTPException(status_code=404, detail="satellite not found")
        norad_id = sat["norad_id"]

        rows: list[dict] = []
        if norad_id is not None:
            cur.execute(
                "SELECT day::date AS day, sma_avg, perigee_min, apogee_max, elset_count "
                "FROM sat_daily WHERE norad_id = %s ORDER BY day",
                (norad_id,),
            )
            rows = cur.fetchall()

    span_days = (rows[-1]["day"] - rows[0]["day"]).days if len(rows) >= 2 else 0
    return {"norad_id": norad_id, "span_days": span_days, "points": _bucket_track(rows)}


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
