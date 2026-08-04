"""CelesTrak consolidated space-weather CSV loader. Raw landing only.

One file carries the whole drag story back to 1957: the eight 3-hourly planetary Kp values
(stored as Kp x 10 in the file, landed verbatim), Ap values and daily average, sunspot number
and the F10.7 solar radio flux family with 81-day means. Past the last observed day the file
continues with INTERPOLATED and PREDICTED rows (F10.7_DATA_TYPE says which), which land too:
the forward rows are the closest thing the platform has to a drag forecast, and filtering is
the view's job, not the landing's.

Same host and politeness posture as the SATCAT and GP loaders (runlog ledger gates the pull);
headers are parsed by name with an explicit header -> column map, because names like
'F10.7_OBS' don't survive a generic snake_case pass.
"""

import csv
import datetime as dt
import io
import logging
from pathlib import Path

from ingest import runlog

logger = logging.getLogger(__name__)

SOURCE = "celestrak"
ENDPOINT = "space_weather"
URL = "https://celestrak.org/SpaceData/SW-All.csv"
# The nightly runs twice a day; 11 hours lets both slots refresh today's provisional row
# instead of the second run always skipping fresh.
MIN_INTERVAL = dt.timedelta(hours=11)
DATA_DIR = Path("data/celestrak")

TABLE = "raw_celestrak_sw"

# CSV header -> column, explicit because of the F10.7 dot. Every header in the file must
# appear here or in _IGNORED; an unknown header raises so a silent format change on
# CelesTrak's side becomes a visible ingest error instead of silently dropped columns.
HEADER_MAP = {
    "DATE": "sw_date",
    "BSRN": "bsrn",
    "ND": "nd",
    "KP1": "kp1", "KP2": "kp2", "KP3": "kp3", "KP4": "kp4",
    "KP5": "kp5", "KP6": "kp6", "KP7": "kp7", "KP8": "kp8",
    "KP_SUM": "kp_sum",
    "AP1": "ap1", "AP2": "ap2", "AP3": "ap3", "AP4": "ap4",
    "AP5": "ap5", "AP6": "ap6", "AP7": "ap7", "AP8": "ap8",
    "AP_AVG": "ap_avg",
    "CP": "cp",
    "C9": "c9",
    "ISN": "isn",
    "F10.7_OBS": "f107_obs",
    "F10.7_ADJ": "f107_adj",
    "F10.7_DATA_TYPE": "f107_data_type",
    "F10.7_OBS_CENTER81": "f107_obs_center81",
    "F10.7_OBS_LAST81": "f107_obs_last81",
    "F10.7_ADJ_CENTER81": "f107_adj_center81",
    "F10.7_ADJ_LAST81": "f107_adj_last81",
}

_COLUMNS = list(HEADER_MAP.values())

_DATE_FIELDS = {"sw_date"}
_NUMERIC_FIELDS = {"cp", "f107_obs", "f107_adj", "f107_obs_center81", "f107_obs_last81",
                   "f107_adj_center81", "f107_adj_last81"}
_TEXT_FIELDS = {"f107_data_type"}
# Everything else is an integer index (Kp x 10, Ap, counts).


def _coerce(field: str, value: str | None):
    """One cell to its column type; un-coercible typed values degrade to NULL (logged), same
    defensive posture as the SATCAT loader."""
    value = (value or "").strip()
    if value == "":
        return None
    try:
        if field in _DATE_FIELDS:
            return dt.date.fromisoformat(value)
        if field in _NUMERIC_FIELDS:
            return float(value)
        if field in _TEXT_FIELDS:
            return value
        return int(value)
    except ValueError:
        logger.warning("space weather: dropping unparseable %s=%r -> NULL", field, value)
        return None


def parse_rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    unknown = [h for h in (reader.fieldnames or []) if h.strip() not in HEADER_MAP]
    if unknown:
        raise ValueError(f"space weather CSV grew unknown headers {unknown}; "
                         "update ingest/celestrak_sw.py::HEADER_MAP deliberately")
    rows = []
    for raw_row in reader:
        row = {}
        for header, value in raw_row.items():
            field = HEADER_MAP[header.strip()]
            row[field] = _coerce(field, value)
        if row.get("sw_date") is not None:  # a dateless row is unusable by every consumer
            rows.append(row)
    return rows


def _land_rows(conn, rows: list[dict], run_id: int) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO {t} ({cols}, ingest_run_id) VALUES ({phs})".format(
                t=TABLE,
                cols=", ".join(_COLUMNS),
                phs=", ".join(["%s"] * (len(_COLUMNS) + 1)),
            ),
            [[row.get(col) for col in _COLUMNS] + [run_id] for row in rows],
        )
    conn.commit()
    return len(rows)


def run(conn) -> int:
    resp = runlog.polite_get(conn, SOURCE, ENDPOINT, URL, MIN_INTERVAL)
    if resp is None:
        logger.info("space weather: skipped, fresh run within %s", MIN_INTERVAL)
        return 0

    rows = parse_rows(resp.text)
    try:
        n = _land_rows(conn, rows, resp.oei_run_id)
    except Exception as exc:
        conn.rollback()
        runlog.finish_run(
            conn, resp.oei_run_id, rows=0, bytes_dl=resp.oei_bytes, status="error",
            notes=str(exc)[:2000],
        )
        raise
    runlog.finish_run(conn, resp.oei_run_id, rows=n, bytes_dl=resp.oei_bytes, status="ok")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / f"sw-{dt.date.today().isoformat()}.csv").write_text(resp.text)
    except OSError as exc:
        logger.warning("space weather: raw-file save failed (%s); rows already landed", exc)
    return n
