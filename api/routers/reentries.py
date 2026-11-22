"""GET /api/reentries/upcoming -- the objects Space-Track predicts will reenter within 60 days.

Space-Track publishes these 60-day decay predictions once a week (ingest/spacetrack_predictions.py,
migration 0029). They are predictions, not decays: nothing here feeds the resolver, the decay
dates or the Conflicts page. The payload is the current set (the newest OK weekly pull), cut to
the predicted dates from today to WINDOW_DAYS ahead, without the objects whose satellite already
carries a decay date, payloads first and then by predicted date. ``as_of`` is the issue date of
the newest prediction in the set; older ones can sit beside it, and each row carries its own. The SPA phrases it on the
Overview as "Reentering soon" (docs/specs/spacetrack-reentry-predictions.md).

The set moves once a week, so it is served from the warm cache like /api/stories.
"""

from __future__ import annotations

import datetime as dt

import psycopg
from fastapi import APIRouter, Depends

from api import cache
from api.deps import get_db
from api.routers.stories import _OWNER_CTE

router = APIRouter(tags=["reentries"])

WINDOW_DAYS = 60

# One statement, so the pull it describes and the rows it returns come from one snapshot: a
# pull committing between two queries would otherwise pair one week's issue date with another
# week's objects. The view's own newest run is the same `newest` run inside one statement. The
# meta columns repeat on every row, and the LEFT JOIN keeps one row of them when the window
# holds no object.
_UPCOMING_SQL = f"""
WITH {_OWNER_CTE},
newest AS (
    SELECT started_at AS pulled_at
    FROM ingest_run
    WHERE source = 'spacetrack' AND endpoint = 'decay_60day' AND status = 'ok'
    ORDER BY ingest_run_id DESC
    LIMIT 1
),
issued AS (SELECT max(msg_epoch) AS issued FROM v_reentry_prediction_latest),
upcoming AS (
    SELECT p.norad_cat_id AS norad_id, p.satellite_id,
           COALESCE(s.canonical_name, p.object_name) AS name, s.object_type, ow.owner,
           p.predicted_date, p.msg_epoch
    FROM v_reentry_prediction_latest p
    LEFT JOIN satellite s ON s.satellite_id = p.satellite_id
    LEFT JOIN owner ow ON ow.satellite_id = p.satellite_id
    WHERE p.predicted_date >= %(today)s
      AND p.predicted_date <= %(today)s::date + %(days)s
      AND s.decay_date IS NULL
)
SELECT newest.pulled_at, issued.issued, u.*
FROM (SELECT 1) one
LEFT JOIN newest ON true
LEFT JOIN issued ON true
LEFT JOIN upcoming u ON true
ORDER BY u.object_type IS DISTINCT FROM 'PAYLOAD', u.predicted_date, u.norad_id
"""

_TYPE_KEYS = {"PAYLOAD": "payload", "ROCKET_BODY": "rocket_body", "DEBRIS": "debris"}


def upcoming_row(r: dict) -> dict:
    return {
        "norad_id": r["norad_id"],
        "satellite_id": r["satellite_id"],
        "name": r["name"],
        "object_type": r["object_type"],
        "owner": r["owner"],
        "predicted_date": r["predicted_date"].isoformat(),
        "msg_epoch": r["msg_epoch"].isoformat() if r["msg_epoch"] else None,
    }


def counts(rows: list[dict]) -> dict:
    """Objects per type (an object the graph has not matched, or of unknown type, counts as
    unknown), plus the Starlink payloads among them, by name as on the stories panels."""
    out = {"payload": 0, "rocket_body": 0, "debris": 0, "unknown": 0, "starlink": 0}
    for r in rows:
        out[_TYPE_KEYS.get(r["object_type"], "unknown")] += 1
        if r["object_type"] == "PAYLOAD" and (r["name"] or "").lower().startswith("starlink"):
            out["starlink"] += 1
    return out


def build_upcoming(db: psycopg.Connection, today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(dt.UTC).date()
    with db.cursor() as cur:
        cur.execute(_UPCOMING_SQL, {"today": today, "days": WINDOW_DAYS})
        fetched = cur.fetchall()
    head = fetched[0]
    rows = [upcoming_row(r) for r in fetched if r["norad_id"] is not None]
    return {
        "as_of": head["issued"].date().isoformat() if head["issued"] else None,
        "pulled_at": head["pulled_at"].isoformat() if head["pulled_at"] else None,
        "window_days": WINDOW_DAYS,
        "predicted_through": (today + dt.timedelta(days=WINDOW_DAYS)).isoformat(),
        "counts": counts(rows),
        "rows": rows,
    }


_cache = cache.register("reentries_upcoming", build_upcoming)


@router.get("/reentries/upcoming")
def get_upcoming(db=Depends(get_db)):
    return _cache.get(db)
