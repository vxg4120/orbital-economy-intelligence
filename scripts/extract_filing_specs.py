"""Extract Schedule S specs for pending filings whose Tech Report has not been parsed yet.

    .venv/bin/python scripts/extract_filing_specs.py
    .venv/bin/python scripts/extract_filing_specs.py --file-number SATAMD2022063000067
    .venv/bin/python scripts/extract_filing_specs.py --if-stale     # nightly mode

Candidate documents are selected by NAME (a '%sched%' pdf) but confirmed by CONTENT
(schedule_s.is_schedule_s) before anything is parsed. The corpus carries 558 distinct doc_name
values across 1,307 documents, so a filename is a hint and never a decision. Nothing is written to
the spec tables for a document that fails the content check.

Every extracted row is validated before it is marked publishable: ingest/spec_validate re-opens the
cited page and confirms the value's tokens are there. Rows that fail are still written, with
is_validated false, because a silent drop would hide extraction regressions; the API serves only
validated rows.

Politeness mirrors ingest/icfs_documents: curl_cffi with Chrome impersonation, because fcc.gov
fronts reject python-requests and plain-curl TLS fingerprints, one warm session across the batch,
and PACING_S between documents. The api-prod attachment gateway intermittently 503s, so each fetch
gets one retry and a failure is recorded on the blob row rather than killing the run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curl_cffi import requests as cr  # noqa: E402

from common.db import get_conn  # noqa: E402
from ingest import filing_blobs, runlog, schedule_s, spec_validate  # noqa: E402

STALE_AFTER = dt.timedelta(days=6)
PACING_S = 2.0

_TODO_SQL = """
SELECT d.file_number, d.sys_id, d.download_url
FROM fcc_filing_document d
LEFT JOIN fcc_spec_filing s ON s.file_number = d.file_number
WHERE d.doc_name ILIKE '%sched%' AND d.doc_name ILIKE '%.pdf'
  AND s.file_number IS NULL
ORDER BY d.file_number
"""

_SCALAR_COLS = (
    "orbit_type", "orbit_type_page",
    "network_name", "network_name_page",
    "lifetime_years", "lifetime_years_page",
    "total_satellites", "total_satellites_page",
)

# Each orbital value is validated against its OWN page: a plane's fields straddle page breaks.
_PLANE_PAGES = {
    "apogee_km": "apogee_page",
    "perigee_km": "perigee_page",
    "inclination_deg": "inclination_page",
    "arg_perigee_deg": "arg_perigee_page",
}


def _session():
    return cr.Session(impersonate="chrome")


def _fetch(session, url: str) -> bytes:
    """One retry: the api-prod gateway intermittently 503s on attachment downloads."""
    last: Exception | None = None
    for _ in range(2):
        try:
            resp = session.get(url, timeout=filing_blobs.FETCH_TIMEOUT_S)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:  # noqa: BLE001 - recorded on the blob row, not swallowed
            last = exc
    raise last  # type: ignore[misc]


def _record_blob_failure(conn, file_number: str, sys_id: str, status: str, note: str,
                         sha: str | None = None, byte_count: int | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO fcc_filing_blob (file_number, sys_id, sha256, byte_count, fetch_status, "
            "fetch_note) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (file_number, sys_id) DO UPDATE SET fetch_status = EXCLUDED.fetch_status, "
            "fetch_note = EXCLUDED.fetch_note, fetched_at = now()",
            (file_number, sys_id, sha, byte_count, status, note[:500]),
        )
    conn.commit()


def process_one(session, conn, run_id: int, file_number: str, sys_id: str, url: str) -> str:
    try:
        data = _fetch(session, url)
    except Exception as exc:  # noqa: BLE001
        _record_blob_failure(conn, file_number, sys_id, "http_error", str(exc))
        return "fetch_failed"

    sha = filing_blobs.sha256_bytes(data)
    if not filing_blobs.looks_complete(data):
        # Server-side truncation, not our parse failure: the gateway answers 200 with
        # content-type application/pdf and sends only the opening bytes. Recorded distinctly so
        # coverage reporting can say "the FCC did not serve this" rather than implying we choked.
        _record_blob_failure(
            conn, file_number, sys_id, "truncated",
            f"gateway returned {len(data)} bytes with no EOF marker", sha, len(data),
        )
        return "truncated"
    try:
        pages = filing_blobs.page_texts(data)
    except Exception as exc:  # noqa: BLE001
        _record_blob_failure(conn, file_number, sys_id, "parse_error", str(exc), sha, len(data))
        return "unreadable"

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO fcc_filing_blob (file_number, sys_id, sha256, byte_count, content_type, "
            "page_count, fetch_status) VALUES (%s, %s, %s, %s, 'application/pdf', %s, 'ok') "
            "ON CONFLICT (file_number, sys_id) DO UPDATE SET sha256 = EXCLUDED.sha256, "
            "byte_count = EXCLUDED.byte_count, page_count = EXCLUDED.page_count, "
            "fetch_status = 'ok', fetch_note = NULL, fetched_at = now()",
            (file_number, sys_id, sha, len(data), len(pages)),
        )
    conn.commit()

    if not schedule_s.is_schedule_s(pages):
        return "not_schedule_s"

    scalars = schedule_s.parse_scalars(pages)
    planes = schedule_s.parse_planes(pages)
    bands = schedule_s.parse_bands(pages)

    # Scalars each carry their own page already, so they validate fieldwise like the planes do.
    # Every served scalar is in this contract. lifetime_years was absent from the first
    # version, which meant it was served without ever being checked against its page; the
    # cross-provider verify pass caught it (spec decision log, 2026-08-24).
    scalar_ok = spec_validate.validate_fieldwise(
        pages, [scalars],
        {"orbit_type": "orbit_type_page", "network_name": "network_name_page",
         "total_satellites": "total_satellites_page", "lifetime_years": "lifetime_years_page"},
    )[0]
    plane_ok = spec_validate.validate_fieldwise(pages, planes, _PLANE_PAGES)
    # service and direction are served, so they are validated: presence of the frequency
    # numbers alone would let a mislabeled service or flipped direction through checked-looking.
    band_ok = spec_validate.validate_rows(
        pages, bands, ("service", "freq_low_mhz", "freq_high_mhz", "direction"))

    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO fcc_spec_filing (file_number, sys_id, doc_sha256, "
            f"{', '.join(_SCALAR_COLS)}, extractor_version, run_id, is_validated) "
            f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(_SCALAR_COLS))}, %s, %s, %s) "
            f"ON CONFLICT (file_number) DO NOTHING",
            (file_number, sys_id, sha, *[scalars.get(c) for c in _SCALAR_COLS],
             schedule_s.EXTRACTOR_VERSION, run_id, scalar_ok),
        )
        for plane, ok in zip(planes, plane_ok):
            cur.execute(
                "INSERT INTO fcc_spec_orbital (file_number, sys_id, plane_idx, apogee_km, "
                "apogee_page, perigee_km, perigee_page, inclination_deg, inclination_page, "
                "arg_perigee_deg, arg_perigee_page, source_page, doc_sha256, extractor_version, "
                "run_id, is_validated) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (file_number, sys_id, plane_idx) DO NOTHING",
                (file_number, sys_id, plane["plane_idx"],
                 plane.get("apogee_km"), plane.get("apogee_page"),
                 plane.get("perigee_km"), plane.get("perigee_page"),
                 plane.get("inclination_deg"), plane.get("inclination_page"),
                 plane.get("arg_perigee_deg"), plane.get("arg_perigee_page"),
                 plane["source_page"], sha, schedule_s.EXTRACTOR_VERSION, run_id, ok),
            )
        for band, ok in zip(bands, band_ok):
            cur.execute(
                "INSERT INTO fcc_spec_band (file_number, sys_id, band_idx, service, freq_low_mhz, "
                "freq_high_mhz, direction, source_page, doc_sha256, extractor_version, run_id, "
                "is_validated) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (file_number, sys_id, band_idx) DO NOTHING",
                (file_number, sys_id, band["band_idx"], band["service"], band["freq_low_mhz"],
                 band["freq_high_mhz"], band["direction"], band["source_page"], sha,
                 schedule_s.EXTRACTOR_VERSION, run_id, ok),
            )
    conn.commit()
    return "extracted"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file-number", help="one filing (dashless dump form)")
    ap.add_argument("--limit", type=int, default=None, help="cap the batch size")
    ap.add_argument("--if-stale", action="store_true",
                    help="skip when the last ok extraction is fresher than a week (nightly mode)")
    args = ap.parse_args()

    conn = get_conn()
    tally: dict[str, int] = {}
    try:
        if args.if_stale and runlog.fresh_within(conn, "fcc", "schedule_s_specs", STALE_AFTER):
            print(f"skipped: last extraction within {STALE_AFTER}")
            return 0
        with conn.cursor() as cur:
            cur.execute(_TODO_SQL)
            todo = cur.fetchall()
        if args.file_number:
            todo = [t for t in todo if t[0] == args.file_number]
        if args.limit:
            todo = todo[: args.limit]

        # A targeted or capped run must not satisfy the nightly's weekly freshness gate: it
        # processed a subset, and letting it confer freshness would silently skip the full sweep
        # for a week. Scoped runs are ledgered under their own endpoint name.
        scoped = bool(args.file_number or args.limit)
        endpoint = "schedule_s_specs_partial" if scoped else "schedule_s_specs"
        run_id = runlog.start_run(conn, "fcc", endpoint)
        session = _session()
        try:
            for i, (file_number, sys_id, url) in enumerate(todo):
                outcome = process_one(session, conn, run_id, file_number, sys_id, url)
                tally[outcome] = tally.get(outcome, 0) + 1
                if i + 1 < len(todo):
                    time.sleep(PACING_S)
        except Exception as exc:
            conn.rollback()
            runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="error",
                              notes=str(exc)[:2000])
            raise
        runlog.finish_run(
            conn, run_id, rows=tally.get("extracted", 0), bytes_dl=0, status="ok",
            notes=", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "nothing to do",
        )
    finally:
        conn.close()

    print("outcomes:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
