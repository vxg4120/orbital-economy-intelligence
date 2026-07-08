"""UCS Satellite Database loader — frozen May 2023 snapshot, optional.

The UCS DB's hosting URL is unstable and the dataset itself is frozen (never treat as current;
it's labeled training data for operator-name matching, per SPEC 4.6). This accepts:
  1. an explicit local file path,
  2. an explicit URL,
  3. or falls back to scanning data/ucs/*.txt|*.csv.
If none of those resolve, this logs a clear message and returns without error — it's an
optional seed, not a required pull.

Only the tab/comma-separated export is supported (no xlsx dependency in this project, by
design — if you have the .xlsx, export it to a delimited text file first).
"""

import csv
import datetime as dt
import glob
import hashlib
import io
import logging
from pathlib import Path

from psycopg.types.json import Jsonb

from ingest import runlog

logger = logging.getLogger(__name__)

SOURCE = "ucs"
MIN_INTERVAL = dt.timedelta(hours=24)
LOCAL_GLOBS = ("data/ucs/*.txt", "data/ucs/*.csv")

# UCS export header (as shipped) -> raw_ucs column
_HEADER_MAP = {
    "name of satellite, alternate names": "name",
    "country of operator/owner": "country_operator",
    "operator/owner": "operator",
    "users": "users",
    "purpose": "purpose",
    "norad number": "norad_id",
    "cospar number": "cospar_id",
    "date of launch": "launch_date",
}

_COLUMNS = [
    "row_key",
    "name",
    "country_operator",
    "operator",
    "users",
    "purpose",
    "norad_id",
    "cospar_id",
    "launch_date",
]


def _row_key(name: str | None, cospar: str | None) -> str:
    basis = f"{name or ''}|{cospar or ''}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()  # noqa: S324 - dedup key, not crypto


def _sniff_dialect(sample: str):
    try:
        return csv.Sniffer().sniff(sample, delimiters="\t,")
    except csv.Error:
        return csv.excel_tab


def parse_rows(text: str) -> list[tuple[dict, dict]]:
    dialect = _sniff_dialect(text[:2048])
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for raw_row in reader:
        typed: dict = {}
        extra: dict = {}
        for header, value in raw_row.items():
            if header is None:
                continue
            cleaned = value.strip() if value and value.strip() else None
            field = _HEADER_MAP.get(header.strip().lower())
            if field:
                typed[field] = cleaned
            else:
                extra[header] = cleaned

        if typed.get("norad_id"):
            try:
                typed["norad_id"] = int(typed["norad_id"])
            except ValueError:
                extra["norad_number_raw"] = typed["norad_id"]
                typed["norad_id"] = None

        typed["row_key"] = _row_key(typed.get("name"), typed.get("cospar_id"))
        rows.append((typed, extra))
    return rows


def _land_rows(conn, rows: list[tuple[dict, dict]], run_id: int) -> int:
    with conn.cursor() as cur:
        for typed, extra in rows:
            values = [typed.get(col) for col in _COLUMNS] + [Jsonb(extra), run_id]
            cur.execute(
                "INSERT INTO raw_ucs ({cols}, extra, ingest_run_id) VALUES ({phs}, %s, %s)".format(
                    cols=", ".join(_COLUMNS),
                    phs=", ".join(["%s"] * len(_COLUMNS)),
                ),
                values,
            )
    conn.commit()
    return len(rows)


def _resolve_local_file() -> Path | None:
    candidates: list[str] = []
    for pattern in LOCAL_GLOBS:
        candidates.extend(glob.glob(pattern))
    return Path(sorted(candidates)[0]) if candidates else None


def run(conn, path_or_url: str | None = None) -> int:
    if path_or_url and path_or_url.startswith(("http://", "https://")):
        resp = runlog.polite_get(conn, SOURCE, "ucs_seed", path_or_url, MIN_INTERVAL)
        if resp is None:
            return 0
        rows = parse_rows(resp.text)
        n = _land_rows(conn, rows, resp.oei_run_id)
        runlog.finish_run(conn, resp.oei_run_id, rows=n, bytes_dl=resp.oei_bytes, status="ok")
        return n

    path = Path(path_or_url) if path_or_url else _resolve_local_file()
    if path is None or not path.exists():
        logger.info(
            "ucs: no local file (data/ucs/*.txt|*.csv) or URL given; skipping optional seed"
        )
        return 0
    if path.suffix.lower() == ".xlsx":
        raise ValueError(
            "UCS .xlsx not supported (no xlsx dependency in this project) — "
            "export to a tab/comma-separated .txt or .csv file first"
        )

    text = path.read_text()
    run_id = runlog.start_run(conn, SOURCE, f"local:{path}")
    rows = parse_rows(text)
    n = _land_rows(conn, rows, run_id)
    runlog.finish_run(conn, run_id, rows=n, bytes_dl=len(text.encode("utf-8")), status="ok")
    return n
