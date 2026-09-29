"""Prune old per-run snapshots from the raw_* landing tables.

Every ingest run lands a FULL copy of its source in raw_*. Nothing ever deleted one, yet every
reader of these tables wants only the newest OK run (identity/churn.py wants the newest two). By
2026-09-28 the older copies filled the disk to 93%. See docs/specs/raw-retention.md, which also
explains why source_assertion, the other table that grows this way, is not pruned here.

The policy, per table:
  * keep the newest KEEP_LATEST OK runs;
  * keep the first OK run of every calendar month (UTC), a monthly sample of history;
  * drop the other runs that FINISHED ('ok' or 'error') before the newest OK run. A run with any
    other status, or none, is kept whatever its id: it may be an ingest still in flight, which
    commits its rows before it records 'ok'.

Modes:
  (default)  dry run: print the plan and change nothing.
  --apply    nightly: DELETE the dropped runs, then VACUUM so that tomorrow's run reuses the
             space. The files stay the size they are, but they stop growing.
  --compact  one-time: rewrite each table down to its kept rows (TRUNCATE and reinsert in one
             transaction), which returns the space to the operating system. It holds an ACCESS
             EXCLUSIVE lock on each table while that table is rewritten, so run it outside the
             07:10 and 19:10 UTC nightly windows.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.db import get_autocommit_conn  # noqa: E402

# identity/churn.py compares the two newest OK raw_gcat_satcat runs, so fewer than two would
# silently blind key-churn detection. Three leaves a run of slack.
KEEP_LATEST = 3

# The ledger statuses that are final: runlog.finish_run writes one of them once and nothing
# changes it after. Only a finished run can be judged safe to drop.
FINISHED = ("ok", "error")

# Every raw_* table, in the order that --compact rewrites them: largest first, so each rewrite
# frees space before the next one needs any. tests/test_prune_snapshots.py fails when a migration
# adds a raw_* table that is missing here, because a table left off this list grows forever.
SNAPSHOT_TABLES = [
    "raw_gcat_satcat",
    "raw_ibfs_frequencies",
    "raw_ibfs_filings",
    "raw_gcat_psatcat",
    "raw_satcat",
    "raw_celestrak_sw",
    "raw_ibfs_space_stations",
    "raw_ibfs_addresses",
    "raw_satnogs_transmitters",
    "raw_gcat_orgs",
    "raw_fcc_ssal",
    "raw_supgp_status",
    "raw_ucs",
]


@dataclass(frozen=True)
class Run:
    """One ingest run's rows in one table, with its ledger entry (status and started_at are None
    when the ledger row is missing)."""

    run_id: int
    status: str | None
    started_at: datetime | None
    rows: int


def runs_to_drop(runs: list[Run], keep_latest: int = KEEP_LATEST) -> list[Run]:
    """The runs that the policy drops, from the runs present in one table. Everything not
    returned is kept, so every doubt keeps."""
    if keep_latest < 2:
        raise ValueError("keep_latest must be at least 2: identity/churn.py compares two runs")
    ok = sorted((r for r in runs if r.status == "ok"), key=lambda r: r.run_id)
    if not ok:
        return []  # no reader sees any of these, and nothing says which are safe to drop
    keep = {r.run_id for r in ok[-keep_latest:]}
    first_of_month: dict[str, int] = {}
    for r in ok:
        if r.started_at is not None:
            first_of_month.setdefault(r.started_at.astimezone(UTC).strftime("%Y-%m"), r.run_id)
    keep |= set(first_of_month.values())
    newest_ok = ok[-1].run_id
    return [
        r
        for r in runs
        if r.status in FINISHED and r.run_id < newest_ok and r.run_id not in keep
    ]


def runs_in(conn, table: str) -> list[Run]:
    """Every run present in the table, with its row count and ledger entry."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT t.ingest_run_id, i.status, i.started_at, t.n
            FROM (SELECT ingest_run_id, count(*) AS n FROM {table} GROUP BY 1) t
            LEFT JOIN ingest_run i USING (ingest_run_id)
            ORDER BY 1
            """
        )
        return [Run(*row) for row in cur.fetchall()]


def delete_runs(conn, table: str, drop: list[Run]) -> int:
    """DELETE the dropped runs' rows. The caller owns the transaction."""
    planned = sum(r.rows for r in drop)
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {table} WHERE ingest_run_id = ANY(%s)", ([r.run_id for r in drop],)
        )
        if cur.rowcount != planned:
            raise RuntimeError(f"{table}: deleted {cur.rowcount} rows, planned {planned}")
    return planned


def compact(conn, table: str, runs: list[Run], drop: list[Run]) -> int:
    """Rewrite the table without the dropped runs' rows, so their space goes back to the
    operating system when the transaction commits. The caller owns the transaction and must have
    locked the table before reading `runs` (prune_table does)."""
    planned = sum(r.rows for r in runs) - sum(r.rows for r in drop)
    with conn.cursor() as cur:
        # Name the columns, skipping generated ones, which cannot be inserted.
        cur.execute(
            "SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped "
            "AND attgenerated = ''",
            (table,),
        )
        cols = cur.fetchone()[0]
        # Created empty and filled by INSERT, because a CREATE TABLE AS cannot take a bound
        # parameter. IS NOT TRUE keeps a row whose run id is NULL: only a match is dropped.
        cur.execute(
            f"CREATE TEMP TABLE _prune_keep ON COMMIT DROP AS SELECT {cols} FROM {table} "
            "WITH NO DATA"
        )
        cur.execute(
            f"INSERT INTO _prune_keep SELECT {cols} FROM {table} "
            "WHERE (ingest_run_id = ANY(%s)) IS NOT TRUE",
            ([r.run_id for r in drop],),
        )
        kept = cur.rowcount
        if kept != planned:
            raise RuntimeError(f"{table}: would keep {kept} rows, planned {planned}")
        cur.execute(f"TRUNCATE {table}")
        # OVERRIDING SYSTEM VALUE: raw_supgp_status's id is a GENERATED ALWAYS identity, and a
        # kept row keeps the id that it already has.
        cur.execute(
            f"INSERT INTO {table} ({cols}) OVERRIDING SYSTEM VALUE SELECT {cols} FROM _prune_keep"
        )
        if cur.rowcount != kept:
            raise RuntimeError(f"{table}: reinserted {cur.rowcount} rows of {kept}")
        cur.execute("DROP TABLE _prune_keep")
    return kept


def prune_table(conn, table: str, mode: str) -> tuple[list[Run], list[Run]]:
    """Apply the policy to one table inside the caller's transaction; return (runs, dropped).

    mode is 'plan' (change nothing), 'delete' (the nightly) or 'compact' (the one-time rewrite).
    Compacting takes its lock BEFORE reading the runs. Read first, and a row committed while the
    TRUNCATE waited for its lock would be truncated with the rest but missing from the copy of
    kept rows (tests/test_prune_snapshots.py races exactly that)."""
    with conn.cursor() as cur:
        # Fail fast rather than queue behind a long transaction, and block everything that
        # queues behind this one.
        cur.execute("SET LOCAL lock_timeout = '10s'")
        if mode == "compact":
            cur.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
    runs = runs_in(conn, table)
    drop = runs_to_drop(runs)
    if drop and mode == "compact":
        compact(conn, table, runs, drop)
    elif drop and mode == "delete":
        delete_runs(conn, table, drop)
    return runs, drop


def _mb(conn, table: str) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_total_relation_size(%s::regclass)", (table,))
        return cur.fetchone()[0] / 2**20


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="delete dropped runs, then VACUUM")
    mode.add_argument(
        "--compact", action="store_true", help="rewrite each table to its kept rows (one-time)"
    )
    args = ap.parse_args()
    how = "compact" if args.compact else "delete" if args.apply else "plan"

    # Autocommit, so that each table gets its own explicit transaction below and VACUUM, which
    # cannot run inside one, can run after them.
    conn = get_autocommit_conn()
    touched: list[str] = []
    try:
        for table in SNAPSHOT_TABLES:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
                    if not cur.fetchone()[0]:
                        print(f"{table}: absent (migration not applied); skipped")
                        continue
                before = _mb(conn, table)
                runs, drop = prune_table(conn, table, how)
                dropped_rows = sum(r.rows for r in drop)
                total_rows = sum(r.rows for r in runs)
                plan = (
                    f"{table}: {len(runs)} runs, drop {len(drop)} "
                    f"({dropped_rows:,} of {total_rows:,} rows)"
                )
                if not drop:
                    print(f"{plan}; nothing to drop")
                elif how == "compact":
                    touched.append(table)
                    print(f"{plan}; compacted, {before:,.0f} MB -> {_mb(conn, table):,.0f} MB")
                elif how == "delete":
                    touched.append(table)
                    print(f"{plan}; deleted")
                else:
                    freed = before * dropped_rows / total_rows
                    print(f"{plan}; dry run, would free ~{freed:,.0f} of {before:,.0f} MB")
        with conn.cursor() as cur:
            for table in touched:
                cur.execute(f"VACUUM (ANALYZE) {table}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
