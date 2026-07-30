"""SatNOGS DB transmitter loader (https://db.satnogs.org). Raw landing only.

SatNOGS DB is the community-curated, moderator-reviewed database of satellite transmitters,
keyed on NORAD id, licensed CC BY-SA 4.0. It is the ground truth for what amateurs can actually
receive, which is a different claim from what a regulator authorized: the two layers stay
separate on purpose, and everything derived from this table must carry SatNOGS attribution.

The transmitters endpoint is paginated (page= query parameter, 404 past the last page); each
page is a JSON array. One nightly pull lands every transmitter with the run id, same
latest-OK-run-wins convention as every other raw table.
"""

import datetime as dt
import json
import logging
from pathlib import Path

from ingest import runlog

logger = logging.getLogger(__name__)

SOURCE = "satnogs"
ENDPOINT = "transmitters"
URL = "https://db.satnogs.org/api/transmitters/?format=json"
MIN_INTERVAL = dt.timedelta(hours=24)
DATA_DIR = Path("data/satnogs")
MAX_PAGES = 200  # ~thousands of transmitters at 25-100/page; a runaway-pagination backstop

_COLUMNS = [
    "uuid", "norad_cat_id", "sat_id", "description", "type", "status", "alive",
    "downlink_low", "downlink_high", "uplink_low", "uplink_high", "mode", "baud",
    "service", "citation", "iaru_coordination", "frequency_violation", "unconfirmed",
    "updated",
]


def _coerce(rec: dict) -> dict:
    """Map one API record onto our columns; unknown keys are ignored, missing become NULL."""
    row = {col: rec.get(col) for col in _COLUMNS}
    if row["updated"]:
        try:
            row["updated"] = dt.datetime.fromisoformat(str(row["updated"]).replace("Z", "+00:00"))
        except ValueError:
            logger.warning("satnogs: unparseable updated=%r -> NULL", row["updated"])
            row["updated"] = None
    return row


def parse_rows(pages: list[list[dict]]) -> list[dict]:
    rows = []
    for page in pages:
        for rec in page:
            rows.append(_coerce(rec))
    return rows


def _land_rows(conn, rows: list[dict], run_id: int) -> int:
    with conn.cursor() as cur:
        for row in rows:
            values = [row.get(col) for col in _COLUMNS] + [run_id]
            cur.execute(
                "INSERT INTO raw_satnogs_transmitters ({cols}, ingest_run_id) "
                "VALUES ({phs})".format(
                    cols=", ".join(_COLUMNS),
                    phs=", ".join(["%s"] * (len(_COLUMNS) + 1)),
                ),
                values,
            )
    conn.commit()
    return len(rows)


def run(conn) -> int:
    """Pull every transmitter page and land the lot under one run. Returns rows landed."""
    import requests

    resp = runlog.polite_get(conn, SOURCE, ENDPOINT, URL, MIN_INTERVAL)
    if resp is None:
        logger.info("satnogs: skipped, fresh run within %s", MIN_INTERVAL)
        return 0
    try:
        first = json.loads(resp.text)
        total_bytes = resp.oei_bytes
        if isinstance(first, list):
            # The endpoint is UNPAGINATED: one response carries the complete transmitter list
            # (~5k records) and a page= parameter is silently ignored, returning the full list
            # again. Verified live 2026-07-29. Looping on page numbers here would re-ingest the
            # same records MAX_PAGES times, so a bare list means we already have everything.
            pages = [first]
        else:
            # Defensive: if SatNOGS ever enables DRF pagination this becomes a dict with
            # 'results' and a 'next' URL. Follow it rather than guessing page numbers.
            pages = [first.get("results", [])]
            next_url = first.get("next")
            fetched = 1
            while next_url and fetched < MAX_PAGES:
                nxt = requests.get(
                    next_url, timeout=runlog.TIMEOUT_S,
                    headers={"User-Agent": runlog.USER_AGENT},
                )
                nxt.raise_for_status()
                payload = json.loads(nxt.text)
                pages.append(payload.get("results", []))
                total_bytes += len(nxt.content)
                next_url = payload.get("next")
                fetched += 1
        rows = parse_rows(pages)
        landed = _land_rows(conn, rows, resp.oei_run_id)
    except Exception as exc:
        conn.rollback()
        runlog.finish_run(
            conn, resp.oei_run_id, rows=0, bytes_dl=resp.oei_bytes, status="error",
            notes=str(exc)[:2000],
        )
        raise
    runlog.finish_run(conn, resp.oei_run_id, rows=landed, bytes_dl=total_bytes, status="ok")
    # Raw-file save is a debugging convenience, best-effort only, after the run is already ok.
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / f"transmitters-{dt.date.today().isoformat()}.json").write_text(
            json.dumps([rec for page in pages for rec in page])
        )
    except OSError as exc:
        logger.warning("satnogs: raw-file save failed (run already ok): %s", exc)
    logger.info("satnogs transmitters: %d rows across %d pages", landed, len(pages))
    return landed
