"""Space-Track 60-day reentry predictions (raw_spacetrack_decay_prediction, migration 0029).

Space-Track publishes, once a week, a prediction for every object it expects to reenter within
60 days: rows of the DECAY class whose source is '60day_msg'. Its API guidelines ask for this
set "Once per week, on Wednesdays after 1700 (UTC)". The nightly runs at 07:10 and 19:10 UTC, so
the Wednesday 19:10 run pulls it. A failed attempt may be retried by a later nightly, but a
week gets at most MAX_ATTEMPTS attempts, all within RETRY_WINDOW after Wednesday 17:00 UTC;
after that the week is missed and the build summary's age check alerts (scripts/build_graph.py).
Every attempt is a ledger row (a crashed one stays 'running' and still counts), so a failing
pull can never turn into a request every nightly, and no pull runs on another weekday, the
first one after a deploy included. Within one attempt the client retries a throttled or failed
request the way it does for every Space-Track query.

The predictions land in their own table under their own ledger endpoint, never in
raw_spacetrack_decay: every reader of that table takes its newest OK landing as the whole set
of actual decays, and a prediction is not a decay. Nothing in identity reads this table; the
SPA shows it as "Reentering soon" (api/routers/reentries.py).
See docs/specs/spacetrack-reentry-predictions.md.
"""

from __future__ import annotations

import datetime as dt
import re

from ingest import runlog, spacetrack_decay
from ingest.spacetrack_client import DECAY_PATH

SOURCE = "spacetrack"
ENDPOINT = "decay_60day"
TABLE = "raw_spacetrack_decay_prediction"
# Eight days of messages: one week's set plus a day of slack.
QUERY = (
    f"{DECAY_PATH}/MSG_EPOCH/%3Enow-8/source/60day_msg"
    "/orderby/NORAD_CAT_ID asc/format/json"
)
PUBLISHED_WEEKDAY = 2  # Wednesday (Monday is 0)
PUBLISHED_HOUR_UTC = 17
# Wednesday 19:10 and the three nightlies after it.
RETRY_WINDOW = dt.timedelta(hours=48)
# The Wednesday 19:10 attempt and one retry.
MAX_ATTEMPTS = 2
# A predicted DECAY_EPOCH is a date, or a date and a time ("2026-11-15 0:00:00" on the DECAY
# rows production lands). Anything else keeps its text and gets no predicted date, rather than
# the loose parse the Historical rows fall back to, which reads "2027" as 1 January.
_EPOCH_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$")


def publication(now: dt.datetime) -> dt.datetime:
    """The most recent Wednesday 17:00 UTC at or before `now`."""
    now = now.astimezone(dt.UTC)
    anchor = (now - dt.timedelta(days=(now.weekday() - PUBLISHED_WEEKDAY) % 7)).replace(
        hour=PUBLISHED_HOUR_UTC, minute=0, second=0, microsecond=0)
    if anchor > now:
        anchor -= dt.timedelta(days=7)
    return anchor


def predicted_date(value) -> dt.date | None:
    """DECAY_EPOCH's date when it is a valid date or date and time, else None."""
    m = _EPOCH_RE.match(str(value or "").strip())
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def parse_row(row: dict) -> dict | None:
    """The Historical parser's columns, with the strict predicted date."""
    parsed = spacetrack_decay.parse_row(row)
    if parsed is not None:
        parsed["decay_date"] = predicted_date(row.get("DECAY_EPOCH"))
    return parsed


def due(conn, now: dt.datetime | None = None) -> bool:
    """True when this week's set should be pulled: `now` is within RETRY_WINDOW after the most
    recent Wednesday 17:00 UTC, no OK pull has started since then, and fewer than MAX_ATTEMPTS
    attempts have."""
    now = now or dt.datetime.now(dt.UTC)
    anchor = publication(now)
    if now - anchor >= RETRY_WINDOW:
        return False
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE status = 'ok'), count(*) FROM ingest_run "
            "WHERE source = %s AND endpoint = %s AND started_at >= %s",
            (SOURCE, ENDPOINT, anchor),
        )
        ok, attempts = cur.fetchone()
    conn.commit()  # end the read, so the caller's next statement starts a fresh transaction
    return ok == 0 and attempts < MAX_ATTEMPTS


def run(conn, client, now: dt.datetime | None = None, out=print) -> int:
    """Pull and land this week's predictions when they are due. Returns the rows landed (0 when
    not due). An empty answer lands as an OK run: the snapshot is honest, and the build summary
    alerts on it."""
    if not due(conn, now):
        out("[spacetrack_predictions] not due: one pull a week, on Wednesdays after "
            f"17:00 UTC (at most {MAX_ATTEMPTS} attempts, within 48 hours)")
        return 0
    resp, run_id = client.query_ledgered(ENDPOINT, QUERY)
    try:
        rows = resp.json()
        if isinstance(rows, dict):
            rows = [rows]
        n = spacetrack_decay.land_rows(conn, rows, run_id, table=TABLE, parse=parse_row,
                                       commit=False)
        # finish_run commits the rows and the OK together: a crash between the two would leave
        # rows no reader sees, and the week due again for a second download.
        runlog.finish_run(conn, run_id, rows=n, bytes_dl=len(resp.content), status="ok",
                          notes=QUERY)
    except Exception as exc:
        conn.rollback()
        runlog.finish_run(conn, run_id, rows=0, bytes_dl=len(resp.content), status="error",
                          notes=str(exc)[:2000])
        raise
    out(f"[spacetrack_predictions] landed {n} 60-day reentry prediction(s)")
    return n
