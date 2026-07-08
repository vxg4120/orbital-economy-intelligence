"""CelesTrak SATCAT bulk CSV loader (the "SKU master"). Raw landing only.

Headers are parsed by name (stdlib csv.DictReader), not by position: CelesTrak's header row
already matches raw_satcat's column names once lower-cased, but we don't rely on column order.
"""

import csv
import datetime as dt
import io
import logging
from pathlib import Path

from ingest import runlog

logger = logging.getLogger(__name__)

SOURCE = "celestrak"
ENDPOINT = "satcat_bulk"
URL = "https://celestrak.org/pub/satcat.csv"
MIN_INTERVAL = dt.timedelta(hours=24)
DATA_DIR = Path("data/celestrak")

_DATE_FIELDS = {"launch_date", "decay_date"}
_NUMERIC_FIELDS = {"period", "inclination", "apogee", "perigee", "rcs"}
_INT_FIELDS = {"norad_cat_id"}

_COLUMNS = [
    "object_name",
    "object_id",
    "norad_cat_id",
    "object_type",
    "ops_status_code",
    "owner",
    "launch_date",
    "launch_site",
    "decay_date",
    "period",
    "inclination",
    "apogee",
    "perigee",
    "rcs",
    "data_status_code",
    "orbit_center",
    "orbit_type",
]


def _snake(header: str) -> str:
    return header.strip().lower().replace("-", "_").replace(" ", "_")


def _coerce(field: str, value: str | None):
    """Coerce one CSV cell to its column type. Defensive: the live catalog is ~60k rows pulled
    exactly once, so a single malformed numeric/date cell must not abort the whole load — an
    un-coercible typed value degrades to NULL (logged) rather than raising."""
    value = (value or "").strip()
    if value == "":
        return None
    try:
        if field in _INT_FIELDS:
            return int(value)
        if field in _NUMERIC_FIELDS:
            return float(value)
        if field in _DATE_FIELDS:
            return dt.date.fromisoformat(value)
    except ValueError:
        logger.warning("satcat: dropping unparseable %s=%r -> NULL", field, value)
        return None
    return value


def parse_rows(text: str) -> list[dict]:
    """Parse SATCAT CSV text into a list of dicts keyed by snake_case column name, typed and
    with empty strings normalized to None."""
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw_row in reader:
        row = {}
        for header, value in raw_row.items():
            field = _snake(header)
            row[field] = _coerce(field, value)
        rows.append(row)
    return rows


def _land_rows(conn, rows: list[dict], run_id: int) -> int:
    with conn.cursor() as cur:
        for row in rows:
            values = [row.get(col) for col in _COLUMNS] + [run_id]
            cur.execute(
                "INSERT INTO raw_satcat ({cols}, ingest_run_id) VALUES ({phs})".format(
                    cols=", ".join(_COLUMNS),
                    phs=", ".join(["%s"] * (len(_COLUMNS) + 1)),
                ),
                values,
            )
    conn.commit()
    return len(rows)


def _save_raw_file(text: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"satcat-{dt.date.today().isoformat()}.csv"
    out_path.write_text(text)
    return out_path


def run(conn) -> int:
    resp = runlog.polite_get(conn, SOURCE, ENDPOINT, URL, MIN_INTERVAL)
    if resp is None:
        logger.info("satcat: skipped, fresh run within %s", MIN_INTERVAL)
        return 0

    rows = parse_rows(resp.text)
    n = _land_rows(conn, rows, resp.oei_run_id)
    _save_raw_file(resp.text)
    runlog.finish_run(conn, resp.oei_run_id, rows=n, bytes_dl=resp.oei_bytes, status="ok")
    return n
