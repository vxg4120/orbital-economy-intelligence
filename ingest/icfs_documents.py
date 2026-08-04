"""ICFS document-inventory fetcher: which PDFs are attached to a space-station filing.

The FCC migrated IBFS's web surface to a ServiceNow portal (ICFS). Its page API answers
anonymously with the full widget tree for a filing's summary page, and buried in that tree is
the attachment table: document names paired with stable download ids on api-prod.fcc.gov.
This module turns a dashless IBFS file number (SATMOD2025061100144, as the bulk dump stores
them) into the portal's dashed form (SAT-MOD-20250611-00144), pulls the page JSON, and walks
the tree for every node that carries both an attachment_name and a download url — keyed on
the FIELDS rather than the widget path, because ServiceNow layouts reshuffle and field names
are the stable part.

Politeness: one page-API call per filing (~300 KB), paced by PACING_S between calls, session
kept warm across a batch. curl_cffi with Chrome impersonation because fcc.gov fronts reject
plain-requests TLS fingerprints (same lesson as ingest/fcc_ssal.py). The api-prod gateway
occasionally answers 503 on document downloads; harvesting only records the ids, so that
flakiness lands on whoever fetches the document bytes, with the retry there.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

PAGE_API = ("https://fccprod.servicenowservices.com/api/now/sp/page"
            "?id=ibfs_application_summary&number={number}")
PACING_S = 2.0

_FILE_NUMBER_LEN = 19  # 'SAT' + 3-char type (may contain '/') + YYYYMMDD + NNNNN


def dashed_file_number(file_number: str) -> str:
    """SATMOD2025061100144 -> SAT-MOD-20250611-00144 (the portal's canonical form).

    Every SAT-subsystem file number in the dump is exactly 19 characters with a 3-character
    type code (LOA, MOD, STA, ..., and the slash-bearing T/C and A/O), so this is a fixed
    slice, not a parse. Anything else raises: silently mangling a file number would harvest
    the wrong filing's documents.
    """
    fn = file_number.strip().upper()
    if len(fn) != _FILE_NUMBER_LEN or not fn.startswith("SAT"):
        raise ValueError(f"unexpected SAT file number shape: {file_number!r}")
    return f"SAT-{fn[3:6]}-{fn[6:14]}-{fn[14:19]}"


def extract_documents(page_json: dict) -> list[dict]:
    """Walk the ServiceNow widget tree for attachment rows.

    A document node is any dict carrying both 'attachment_name' and a download 'url'; its
    enclosing all_data row usually carries the display date in a sibling field. Deduped on
    the download id (the same node can appear in more than one widget)."""
    docs: dict[str, dict] = {}

    def walk(node, row_date=None):
        if isinstance(node, dict):
            created = node.get("sys_created_on")
            if isinstance(created, dict):
                created = created.get("display_value") or created.get("value")
            date = created or row_date
            name = node.get("attachment_name")
            url = node.get("url")
            if name and isinstance(url, str) and "/icfs-attachment/" in url:
                sys_id = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
                docs.setdefault(sys_id, {
                    "sys_id": sys_id,
                    "doc_name": str(name).strip(),
                    "doc_date": _clean_date(date),
                    "download_url": url.split("?")[0],
                })
            for v in node.values():
                walk(v, date)
        elif isinstance(node, list):
            for v in node:
                walk(v, row_date)

    walk(page_json)
    return list(docs.values())


def _clean_date(value):
    if not value or not isinstance(value, str):
        return None
    v = value.strip()[:10]
    return v if len(v) == 10 and v[4] == "-" else None


def fetch_documents(session, file_number: str) -> list[dict]:
    """One filing's document inventory via the anonymous page API. Raises on HTTP failure."""
    number = dashed_file_number(file_number)
    resp = session.get(PAGE_API.format(number=number), timeout=90,
                       headers={"Accept": "application/json"})
    resp.raise_for_status()
    return extract_documents(json.loads(resp.text))


def harvest(conn, file_numbers: list[str], pacing_s: float = PACING_S) -> dict:
    """Fetch and upsert document inventories for the given filings. Returns counts.

    Existing rows are refreshed (name/date/url can be corrected upstream) and never deleted:
    an attachment that later vanishes from the portal remains on record here.
    """
    from curl_cffi import requests as cr

    session = cr.Session(impersonate="chrome")
    filings_done = 0
    docs_seen = 0
    failures: list[str] = []
    with conn.cursor() as cur:
        for i, fn in enumerate(file_numbers):
            if i:
                time.sleep(pacing_s)
            try:
                docs = fetch_documents(session, fn)
            except Exception as exc:  # noqa: BLE001 - one bad filing must not stop the batch
                logger.warning("icfs documents: %s failed: %s", fn, str(exc)[:200])
                failures.append(fn)
                continue
            for d in docs:
                cur.execute(
                    """
                    INSERT INTO fcc_filing_document
                        (file_number, sys_id, doc_name, doc_date, download_url)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (file_number, sys_id) DO UPDATE
                    SET doc_name = EXCLUDED.doc_name,
                        doc_date = COALESCE(EXCLUDED.doc_date, fcc_filing_document.doc_date),
                        download_url = EXCLUDED.download_url,
                        fetched_at = now()
                    """,
                    (fn, d["sys_id"], d["doc_name"], d["doc_date"], d["download_url"]),
                )
            filings_done += 1
            docs_seen += len(docs)
    conn.commit()
    return {"filings": filings_done, "documents": docs_seen, "failures": failures}
