"""GET /api/reentries/upcoming -- the objects Space-Track predicts will reenter within 60 days.

Space-Track publishes these 60-day decay predictions once a week (ingest/spacetrack_predictions.py,
migration 0029). They are predictions, not decays: nothing here feeds the resolver, the decay
dates or the Conflicts page. The payload is the current set (the newest OK weekly pull), cut to
the predicted dates from today to WINDOW_DAYS ahead, without the objects whose satellite already
carries a decay date, payloads first and then by predicted date. The SPA phrases it on the
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

# The newest OK pull, from the ledger (a pull that landed nothing still counts), and the newest
# prediction epoch in the current set.
_META_SQL = """
SELECT i.started_at AS pulled_at, i.rows_ingested AS rows,
       (SELECT max(msg_epoch) FROM v_reentry_prediction_latest) AS issued
FROM ingest_run i
WHERE i.source = 'spacetrack' AND i.endpoint = 'decay_60day' AND i.status = 'ok'
ORDER BY i.ingest_run_id DESC
LIMIT 1
"""

_UPCOMING_SQL = f"""
WITH {_OWNER_CTE}
SELECT p.norad_cat_id AS norad_id, p.satellite_id,
       COALESCE(s.canonical_name, p.object_name) AS name, s.object_type, ow.owner,
       p.predicted_date, p.msg_epoch
FROM v_reentry_prediction_latest p
LEFT JOIN satellite s ON s.satellite_id = p.satellite_id
LEFT JOIN owner ow ON ow.satellite_id = p.satellite_id
WHERE p.predicted_date >= %(today)s
  AND p.predicted_date <= %(today)s::date + %(days)s
  AND s.decay_date IS NULL
ORDER BY s.object_type IS DISTINCT FROM 'PAYLOAD', p.predicted_date, p.norad_cat_id
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
        cur.execute(_META_SQL)
        meta = cur.fetchone()
        cur.execute(_UPCOMING_SQL, {"today": today, "days": WINDOW_DAYS})
        rows = [upcoming_row(r) for r in cur.fetchall()]
    issued = meta["issued"] if meta else None
    return {
        "as_of": issued.date().isoformat() if issued else None,
        "pulled_at": meta["pulled_at"].isoformat() if meta else None,
        "window_days": WINDOW_DAYS,
        "predicted_through": (today + dt.timedelta(days=WINDOW_DAYS)).isoformat(),
        "counts": counts(rows),
        "rows": rows,
    }


_cache = cache.register("reentries_upcoming", build_upcoming)


@router.get("/reentries/upcoming")
def get_upcoming(db=Depends(get_db)):
    return _cache.get(db)
