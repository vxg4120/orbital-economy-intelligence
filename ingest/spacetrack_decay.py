"""Space-Track actual-decay landing (raw_spacetrack_decay, migration 0027). Raw landing only.

One query a day: the decay messages of type 'Historical' (an object that has actually
reentered; the other types are predictions) whose MSG_EPOCH falls in the last WINDOW_DAYS days.
Space-Track's DECAY class is limited to one query a day, so the ledger gate is 23 hours rather
than runlog.DAILY's 20, which would let two pulls land inside one 24-hour span. Predictions are
not pulled: the 60-day predictions inside the same class are limited to one query a week
(docs/specs/spacetrack-topup.md, Open questions).

The view v_spacetrack_decay_actual reads the newest OK run of this table. The resolver does not
read it yet; that is the spec's next step.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from identity.normalize import parse_date_loose
from ingest import runlog
from ingest.celestrak_gp import _parse_utc
from ingest.spacetrack_client import DECAY_PATH

logger = logging.getLogger(__name__)

SOURCE = "spacetrack"
ENDPOINT = "decay"
TABLE = "raw_spacetrack_decay"
WINDOW_DAYS = 30
MIN_INTERVAL = dt.timedelta(hours=23)
QUERY = (
    f"{DECAY_PATH}/MSG_TYPE/Historical/MSG_EPOCH/%3Enow-{WINDOW_DAYS}"
    "/orderby/NORAD_CAT_ID asc/format/json"
)

_COLUMNS = ["norad_cat_id", "object_name", "intldes", "country", "msg_epoch", "decay_epoch",
            "decay_date", "source", "msg_type", "precedence", "payload", "ingest_run_id"]


def _int(value):
    return None if value in (None, "") else int(value)


def _decay_date(value) -> dt.date | None:
    """DECAY_EPOCH's date. Space-Track sends '2026-10-01' or '2026-10-01 12:34:00'; anything
    else degrades to NULL (the verbatim text stays in decay_epoch)."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return parse_date_loose(text)


def _msg_epoch(value):
    if value in (None, ""):
        return None
    try:
        return _parse_utc(str(value).replace(" ", "T", 1))
    except ValueError:
        return None


def parse_row(row: dict) -> dict | None:
    """One message to its column values, or None when it has no NORAD id (unusable)."""
    norad = _int(row.get("NORAD_CAT_ID"))
    if norad is None:
        return None
    return {
        "norad_cat_id": norad,
        "object_name": row.get("OBJECT_NAME"),
        "intldes": row.get("INTLDES") or row.get("OBJECT_ID"),
        "country": row.get("COUNTRY"),
        "msg_epoch": _msg_epoch(row.get("MSG_EPOCH")),
        "decay_epoch": row.get("DECAY_EPOCH"),
        "decay_date": _decay_date(row.get("DECAY_EPOCH")),
        "source": row.get("SOURCE"),
        "msg_type": row.get("MSG_TYPE"),
        "precedence": _int(row.get("PRECEDENCE")),
        "payload": json.dumps(row),
    }


def land_rows(conn, rows: list[dict], run_id: int) -> int:
    parsed = [p for p in (parse_row(r) for r in rows) if p is not None]
    if len(parsed) < len(rows):
        logger.warning("decay: skipped %d message(s) without a NORAD id", len(rows) - len(parsed))
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {TABLE} ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join(['%s'] * len(_COLUMNS))})",
            [[p[c] for c in _COLUMNS[:-1]] + [run_id] for p in parsed],
        )
    conn.commit()
    return len(parsed)


def run(conn, client, out=print) -> int:
    """Pull and land the window unless a pull finished OK within MIN_INTERVAL. Returns the
    rows landed (0 when skipped)."""
    if runlog.fresh_within(conn, SOURCE, ENDPOINT, MIN_INTERVAL):
        run_id = runlog.start_run(conn, SOURCE, ENDPOINT)
        runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="skipped_fresh")
        out(f"[spacetrack_decay] skipped: an OK pull finished within {MIN_INTERVAL}")
        return 0
    resp, run_id = client.query_ledgered(ENDPOINT, QUERY)
    try:
        rows = resp.json()
        if isinstance(rows, dict):
            rows = [rows]
        n = land_rows(conn, rows, run_id)
    except Exception as exc:
        conn.rollback()
        runlog.finish_run(conn, run_id, rows=0, bytes_dl=len(resp.content), status="error",
                          notes=str(exc)[:2000])
        raise
    runlog.finish_run(conn, run_id, rows=n, bytes_dl=len(resp.content), status="ok",
                      notes=QUERY)
    out(f"[spacetrack_decay] landed {n} actual-decay message(s), MSG_EPOCH > now-{WINDOW_DAYS}")
    return n
