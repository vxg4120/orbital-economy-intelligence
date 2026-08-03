"""One-shot IBFS re-land, bypassing the 72h freshness gate. For schema-extension backfills.

The nightly ingest gates IBFS pulls to one per 72 hours out of politeness to ftp.fcc.gov.
That gate is correct for the steady state and wrong for the moment a migration adds columns
the last landed run predates (raw rows are immutable per run; new columns stay NULL until the
next landing). This script lands a fresh run immediately, preferring a saved zip from
data/fcc/ so the usual case costs the FCC nothing.

    .venv/bin/python scripts/backfill_ibfs.py [--zip data/fcc/ibfs-2026-08-02.zip]

Without --zip it uses the newest saved data/fcc/ibfs-*.zip, and only downloads when none
exists. Lands as a normal 'ok' ingest_run (notes say backfill + source), so the
latest-OK-run views pick it up exactly like a nightly landing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.db import get_conn  # noqa: E402
from ingest import ibfs, runlog  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", dest="zip_path", default=None,
                    help="saved IBFS.zip to land (default: newest data/fcc/ibfs-*.zip)")
    args = ap.parse_args()

    zip_path = Path(args.zip_path) if args.zip_path else None
    if zip_path is None:
        saved = sorted(ibfs.DATA_DIR.glob("ibfs-*.zip"))
        zip_path = saved[-1] if saved else None

    if zip_path is not None:
        content = zip_path.read_bytes()
        source_note = f"backfill from {zip_path.name}"
    else:
        print("no saved zip found; downloading from ftp.fcc.gov")
        content = ibfs._fetch()
        source_note = "backfill via fresh download"

    conn = get_conn()
    try:
        run_id = runlog.start_run(conn, ibfs.SOURCE, ibfs.ENDPOINT)
        try:
            tables = ibfs.parse_zip(content)
            landed = ibfs._land_rows(conn, tables, run_id)
        except Exception as exc:
            conn.rollback()
            runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="error",
                              notes=str(exc)[:2000])
            raise
        notes = source_note + "; " + ", ".join(
            f"{table}={len(tables[table])}" for _, (table, _) in ibfs.MEMBERS.items())
        runlog.finish_run(conn, run_id, rows=landed, bytes_dl=len(content), status="ok",
                          notes=notes)
    finally:
        conn.close()
    print(f"landed {landed} rows ({notes})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
