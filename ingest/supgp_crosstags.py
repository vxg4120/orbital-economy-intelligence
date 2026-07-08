"""CelesTrak Supplemental GP (SupGP) cross-tag anomaly scraper — best effort.

SupGP match results expose per-constellation NO MATCH / cross-tag counts between operator-declared
ephemerides and the public catalog — entity-resolution signals published by CelesTrak itself
(SPEC 4.3). The index page at celestrak.org/NORAD/elements/supplemental/ is a plain HTML listing,
not a documented API, and its structure can change without notice.

Real structure (verified live 2026-07-07): each constellation sits in a table cell that opens with
its display name and a SupGP data link (``sup-gp.php?FILE=<tag>``), followed by a "Matching Results"
link whose badge ``<b class=warn|good>N</b>`` reports the number of that constellation's SupGP
objects with a NO MATCH / cross-tag anomaly — ``class=warn`` (N>0) flags anomalies, ``class=good``
(N=0) means fully matched. The literal string "NO MATCH" appears only once, in a legend paragraph
(with non-numeric ``<b class=warn>red</b>`` / ``<b class=good>green</b>`` badges) — NOT in any data
cell. This parser therefore records only NUMERIC badges found inside a Matching-Results anchor, and
lands one row per constellation whose warn count is > 0 (flag='MATCH_WARNINGS', detail carrying the
count). If badges parse but all are ``good`` the run is a clean 'ok' with a note; if the page yields
NO badges at all the structure has changed and the run is recorded 'error' so it can't hide.
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

MATCH_WARNINGS_FLAG = "MATCH_WARNINGS"

_FILE_TAG_RE = re.compile(r"FILE=([A-Za-z0-9_-]+)", re.IGNORECASE)
_MATCHING_TAG_RE = re.compile(r"MATCHING=([A-Za-z0-9_-]+)", re.IGNORECASE)
_COUNT_RE = re.compile(r"^\d[\d,]*$")


def _tag_from_file_href(href: str) -> str | None:
    m = _FILE_TAG_RE.search(href or "")
    return m.group(1).lower() if m else None


def _is_match_href(href: str) -> bool:
    href = href or ""
    return "matches.php" in href or "MATCHING=" in href


def _tag_from_match_href(href: str) -> str | None:
    """Constellation tag from a Matching-Results anchor: 'starlink/matches.php' -> 'starlink',
    'table-matching.php?MATCHING=ses' -> 'ses'."""
    href = href or ""
    m = _MATCHING_TAG_RE.search(href)
    if m:
        return m.group(1).lower()
    if "/matches.php" in href:
        return href.split("/matches.php")[0].rsplit("/", 1)[-1].lower() or None
    return None


def _parse_count(text: str) -> int | None:
    text = (text or "").strip()
    if not _COUNT_RE.match(text):
        return None  # non-numeric (e.g. legend 'red'/'green') -> not a data badge
    return int(text.replace(",", ""))


class _MatchBadgeExtractor(HTMLParser):
    """Collect every numeric 'Matching Results' badge on the SupGP index as a per-constellation
    dict: {file_tag, object_name, badge_class ('warn'|'good'), count}."""

    def __init__(self):
        super().__init__()
        self.badges: list[dict] = []
        self._href: str | None = None
        self._in_badge = False
        self._badge_class: str | None = None
        self._badge_text: list[str] = []
        self._in_cell = False
        self._cell_lead: list[str] = []
        self._cell_lead_done = False
        self._cell_file_tag: str | None = None

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag in ("td", "th"):
            self._in_cell = True
            self._cell_lead = []
            self._cell_lead_done = False
            self._cell_file_tag = None
        elif tag == "a":
            self._href = ad.get("href", "") or ""
            if self._cell_file_tag is None:
                self._cell_file_tag = _tag_from_file_href(self._href)
            self._cell_lead_done = True  # display name is the text before the first link
        elif tag == "b" and ad.get("class") in ("warn", "good"):
            self._in_badge = True
            self._badge_class = ad.get("class")
            self._badge_text = []

    def handle_endtag(self, tag):
        if tag == "b" and self._in_badge:
            count = _parse_count("".join(self._badge_text))
            href = self._href or ""
            if count is not None and _is_match_href(href):
                file_tag = _tag_from_match_href(href) or self._cell_file_tag
                name = "".join(self._cell_lead).strip() or file_tag
                self.badges.append(
                    {
                        "file_tag": file_tag,
                        "object_name": name,
                        "badge_class": self._badge_class,
                        "count": count,
                    }
                )
            self._in_badge = False
            self._badge_class = None
            self._badge_text = []
        elif tag == "a":
            self._href = None
        elif tag in ("td", "th"):
            self._in_cell = False

    def handle_data(self, data):
        if self._in_badge:
            self._badge_text.append(data)
        elif self._in_cell and not self._cell_lead_done:
            self._cell_lead.append(data)


def parse_match_badges(html: str) -> list[dict]:
    """Every numeric Matching-Results badge on the page (both warn and good), one per constellation."""
    parser = _MatchBadgeExtractor()
    parser.feed(html)
    return parser.badges


def _anomaly_rows(badges: list[dict]) -> list[dict]:
    """One raw_supgp_status row per constellation reporting cross-tag anomalies (warn count > 0)."""
    rows = []
    for b in badges:
        if b["badge_class"] == "warn" and b["count"] > 0:
            rows.append(
                {
                    "norad_id": None,
                    "object_name": b["object_name"],
                    "file_tag": b["file_tag"],
                    "flag": MATCH_WARNINGS_FLAG,
                    "detail": f"{b['count']} SupGP objects with NO MATCH / cross-tag anomaly "
                    f"({b['file_tag']})",
                }
            )
    return rows


def extract_anomaly_rows(html: str) -> list[dict]:
    """Parse the SupGP index HTML into raw_supgp_status row dicts for anomalous constellations."""
    return _anomaly_rows(parse_match_badges(html))


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

    badges = parse_match_badges(resp.text)
    anomalies = _anomaly_rows(badges)
    n = _land_rows(conn, anomalies, resp.oei_run_id)

    if not badges:
        # Zero Matching-Results badges of any kind -> the page structure changed under us. Record
        # 'error' rather than a silent 'ok' with 0 rows so it surfaces instead of hiding.
        logger.warning("supgp: no Matching Results badges found — page structure may have changed")
        runlog.finish_run(
            conn, resp.oei_run_id, rows=n, bytes_dl=resp.oei_bytes, status="error",
            notes="no Matching Results badges found on SupGP index page (structure changed?)",
        )
    else:
        note = (
            f"{n} constellation(s) with cross-tag match warnings"
            if anomalies
            else "all constellations report class=good (no cross-tag warnings)"
        )
        runlog.finish_run(
            conn, resp.oei_run_id, rows=n, bytes_dl=resp.oei_bytes, status="ok", notes=note
        )
    return n
