"""CelesTrak Supplemental GP (SupGP) cross-tag anomaly scraper — best effort.

SupGP match results expose NO MATCH / mismatch / cross-tag flags between operator-declared
objects and the catalog — entity-resolution signals published by CelesTrak itself (SPEC 4.3).
The index page at celestrak.org/NORAD/elements/supplemental/ is a plain HTML listing, not a
documented API, and its structure can change without notice. This parses defensively: it walks
every <table> row looking for anomaly keywords and lands whatever it can find. If nothing
parseable is found, it lands zero rows with an `ok` run and an explanatory note rather than
failing — a structure change here should never break the rest of the ingestion cycle.
"""

import datetime as dt
import logging
import re
from html.parser import HTMLParser

from ingest import runlog

logger = logging.getLogger(__name__)

SOURCE = "celestrak"
ENDPOINT = "supgp_index"
URL = "https://celestrak.org/NORAD/elements/supplemental/"
MIN_INTERVAL = dt.timedelta(hours=24)

ANOMALY_KEYWORDS = ("NO MATCH", "NO-MATCH", "MISMATCH", "CROSS-TAG", "CROSSTAG")
_NORAD_CELL_RE = re.compile(r"\d{3,9}")


class _TableRowExtractor(HTMLParser):
    """Collects the text content of every <tr> as a list of <td>/<th> cell strings."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_row = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_row = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in_row:
            self._current_row.append("".join(self._current_cell).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._current_row:
                self.rows.append(self._current_row)
            self._in_row = False

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell.append(data)


def extract_anomaly_rows(html: str) -> list[dict]:
    parser = _TableRowExtractor()
    parser.feed(html)

    anomalies = []
    for cells in parser.rows:
        row_text = " ".join(cells)
        upper = row_text.upper()
        flag = next((kw for kw in ANOMALY_KEYWORDS if kw in upper), None)
        if not flag:
            continue

        norad_id = None
        object_name = None
        for cell in cells:
            stripped = cell.strip()
            if norad_id is None and _NORAD_CELL_RE.fullmatch(stripped):
                norad_id = int(stripped)
            elif object_name is None and re.search(r"[A-Za-z]", cell) and cell.strip().upper() not in ANOMALY_KEYWORDS:
                object_name = stripped

        anomalies.append(
            {
                "norad_id": norad_id,
                "object_name": object_name,
                "file_tag": cells[0] if cells else None,
                "flag": flag,
                "detail": row_text.strip(),
            }
        )
    return anomalies


def _land_rows(conn, rows: list[dict], run_id: int) -> int:
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                "INSERT INTO raw_supgp_status "
                "(norad_id, object_name, file_tag, flag, detail, ingest_run_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    row.get("norad_id"),
                    row.get("object_name"),
                    row.get("file_tag"),
                    row.get("flag"),
                    row.get("detail"),
                    run_id,
                ),
            )
    conn.commit()
    return len(rows)


def run(conn) -> int:
    resp = runlog.polite_get(conn, SOURCE, ENDPOINT, URL, MIN_INTERVAL)
    if resp is None:
        logger.info("supgp: skipped, fresh run within %s", MIN_INTERVAL)
        return 0

    anomalies = extract_anomaly_rows(resp.text)
    n = _land_rows(conn, anomalies, resp.oei_run_id)
    note = None if anomalies else "no parseable NO MATCH/cross-tag rows found on index page"
    runlog.finish_run(
        conn, resp.oei_run_id, rows=n, bytes_dl=resp.oei_bytes, status="ok", notes=note
    )
    return n
