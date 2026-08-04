"""Harvest ICFS document inventories for pending filings + load curated filing notes.

    .venv/bin/python scripts/fetch_filing_documents.py            # linked-cohort filings
    .venv/bin/python scripts/fetch_filing_documents.py --all      # every pending filing
    .venv/bin/python scripts/fetch_filing_documents.py --file-number SATMOD2025061100144

Default scope is the pending applications whose applicant FRN is curated to a Bus Benchmarks
cohort (the filings that actually surface on cohort pages), which keeps the default run to a
couple hundred paced page-API calls. --all covers the whole pending queue when wanted. Runs
are recorded in the ingest ledger (source fcc / endpoint icfs_documents) with per-run counts;
freshness gating is by scope-appropriate restraint rather than a hard interval, because the
pending set changes by a handful of filings per week.

Also (re)loads identity/fcc_filing_notes.yml into fcc_filing_note: curated, page-cited
reading notes for high-value filings, reviewable in git, replace-all on every load.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from common.db import get_conn  # noqa: E402
from ingest import icfs_documents, runlog  # noqa: E402

# The pending set shifts by a handful of filings per week, so the nightly runs this with
# --if-stale and the ledger gates the actual harvest to roughly weekly: 136 paced page-API
# calls twice a day would be pointless load on the portal for data that barely moves.
STALE_AFTER = dt.timedelta(days=6)

NOTES_YML = Path(__file__).resolve().parent.parent / "identity" / "fcc_filing_notes.yml"

_LINKED_SQL = """
SELECT p.file_number FROM v_fcc_pending_applications p
JOIN fcc_applicant_link l ON l.frn = p.applicant_frn
ORDER BY p.date_filed DESC
"""
_ALL_SQL = "SELECT file_number FROM v_fcc_pending_applications ORDER BY date_filed DESC"


def load_notes(conn) -> int:
    if not NOTES_YML.exists():
        return 0
    spec = yaml.safe_load(NOTES_YML.read_text(encoding="utf-8")) or {}
    notes = spec.get("notes", [])
    with conn.cursor() as cur:
        cur.execute("DELETE FROM fcc_filing_note")
        for n in notes:
            cur.execute(
                """
                INSERT INTO fcc_filing_note
                    (file_number, summary, key_points, source_doc, source_pages, noted_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (n["file_number"], n["summary"], n.get("key_points"),
                 n.get("source_doc"), n.get("source_pages"), n["noted_at"]),
            )
    conn.commit()
    return len(notes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true", help="every pending filing")
    scope.add_argument("--file-number", help="one filing (dashless dump form)")
    ap.add_argument("--limit", type=int, default=None, help="cap the batch size")
    ap.add_argument("--if-stale", action="store_true",
                    help="skip when the last ok harvest is fresher than a week (nightly mode)")
    args = ap.parse_args()

    conn = get_conn()
    try:
        if args.if_stale and runlog.fresh_within(conn, "fcc", "icfs_documents", STALE_AFTER):
            print(f"skipped: last harvest within {STALE_AFTER}")
            return 0
        with conn.cursor() as cur:
            if args.file_number:
                numbers = [args.file_number]
            else:
                cur.execute(_ALL_SQL if args.all else _LINKED_SQL)
                numbers = [r[0] for r in cur.fetchall()]
        if args.limit:
            numbers = numbers[: args.limit]

        run_id = runlog.start_run(conn, "fcc", "icfs_documents")
        try:
            stats = icfs_documents.harvest(conn, numbers)
        except Exception as exc:
            conn.rollback()
            runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="error",
                              notes=str(exc)[:2000])
            raise
        notes_n = load_notes(conn)
        runlog.finish_run(
            conn, run_id, rows=stats["documents"], bytes_dl=0, status="ok",
            notes=(f"filings={stats['filings']}, documents={stats['documents']}, "
                   f"failures={len(stats['failures'])}, notes={notes_n}"),
        )
    finally:
        conn.close()

    print(f"harvested {stats['documents']} documents across {stats['filings']} filings "
          f"({len(stats['failures'])} failures); {notes_n} curated notes loaded")
    if stats["failures"]:
        print("failed file numbers:", ", ".join(stats["failures"][:10]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
