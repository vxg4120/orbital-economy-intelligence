#!/usr/bin/env python
"""CLI entry point for the ingestion layer.

Runs the loaders in politeness-safe order (satcat -> gcat -> gp -> supgp -> ucs), each wrapped
in its own try/except so one source failing doesn't stop the rest, then prints the resulting
ingest_run rows as a table.

Usage: python scripts/ingest_all.py [--source satcat|gp|gcat|ucs|supgp|all]
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.db import get_conn
from ingest import celestrak_gp, celestrak_satcat, gcat_loader, supgp_crosstags, ucs_seed

# Execution order (politeness-safe: cheapest/most-stable sources first).
_ORDER = ["satcat", "gcat", "gp", "supgp", "ucs"]

_RUNNERS = {
    "satcat": celestrak_satcat.run,
    "gcat": gcat_loader.run,
    "gp": celestrak_gp.run,
    "supgp": supgp_crosstags.run,
    "ucs": ucs_seed.run,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run OEI ingestion loaders.")
    parser.add_argument(
        "--source",
        choices=["satcat", "gp", "gcat", "ucs", "supgp", "all"],
        default="all",
        help="Which loader to run (default: all, in politeness-safe order).",
    )
    return parser


def _print_run_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ingest_run_id, source, endpoint, status, rows_ingested, "
            "bytes_downloaded, started_at, finished_at "
            "FROM ingest_run ORDER BY ingest_run_id DESC LIMIT 20"
        )
        rows = cur.fetchall()
        columns = [desc.name for desc in cur.description]

    str_rows = [[str(v) for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))

    def _fmt(vals):
        return " | ".join(v.ljust(widths[i]) for i, v in enumerate(vals))

    print(_fmt(columns))
    print("-+-".join("-" * w for w in widths))
    for row in str_rows:
        print(_fmt(row))


def main(argv: list[str] | None = None, conn=None) -> int:
    args = _build_parser().parse_args(argv)
    sources = _ORDER if args.source == "all" else [args.source]

    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        for name in sources:
            try:
                _RUNNERS[name](conn)
            except Exception as exc:  # one source's failure must not stop the rest
                print(f"[ingest_all] {name} failed: {exc}", file=sys.stderr)
                # A DB-level failure (as opposed to an HTTP error, which polite_get already
                # commits before raising) leaves the transaction aborted; without a rollback
                # every subsequent loader's first query would cascade-fail too.
                conn.rollback()
        _print_run_table(conn)
    finally:
        if owns_conn:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
