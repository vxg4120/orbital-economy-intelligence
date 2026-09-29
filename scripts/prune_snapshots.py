"""Prune old per-run snapshots from the raw_* landing tables and source_assertion.

Every ingest run lands a FULL copy of its source in raw_*, and identity/assertions.py writes a
full set of assertions for every run. Nothing ever deleted one, yet every reader wants only the
newest OK run (identity/churn.py wants the newest two). By 2026-09-28 the older copies were
8.8 of the 10.5 GB these tables held and the disk was at 93%. See docs/specs/raw-retention.md.

The policy, per stream (one feed's runs in one table; see SNAPSHOT_TABLES):
  * keep the newest KEEP_LATEST OK runs;
  * keep the first OK run of every calendar month (UTC), so that a month's published numbers
    can still be traced to the inputs that produced them;
  * keep every run newer than the newest OK run: an ingest still in flight, which no reader
    sees yet and which this must never touch;
  * drop everything else, including failed runs older than the newest OK run, since no reader
    ever selects a failed run.

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

# Every table whose rows are per-run snapshots, in the order that --compact rewrites them: the
# raw tables first, so each rewrite frees space before the next one needs any, and
# source_assertion (the largest) last, when the disk has the most room for its temporary copy.
# Each maps to how its runs split into streams, and the policy applies per stream:
#   None                 every run in the table is one feed's snapshot: one stream.
#   (column, streams)    the column that tells the table's feeds apart, and the feeds that
#                        re-assert everything on every run. Only those are pruned; any other
#                        source_assertion source (a one-off operator correction, say) is kept
#                        whole, because nothing would ever re-assert it.
# Never split on ingest_run.source: SATCAT runs are logged as 'celestrak', a label that GP, space
# weather and SupGP share. tests/test_prune_snapshots.py fails when a migration adds a raw_*
# table that is missing here, because a table left off this list grows forever.
SNAPSHOT_TABLES: dict[str, tuple[str, tuple[str, ...]] | None] = {
    "raw_gcat_satcat": None,
    "raw_ibfs_frequencies": None,
    "raw_ibfs_filings": None,
    "raw_gcat_psatcat": None,
    "raw_satcat": None,
    "raw_celestrak_sw": None,
    "raw_ibfs_space_stations": None,
    "raw_ibfs_addresses": None,
    "raw_satnogs_transmitters": None,
    "raw_gcat_orgs": None,
    "raw_fcc_ssal": None,
    "raw_supgp_status": None,
    "raw_ucs": None,
    "source_assertion": ("source", ("satcat", "gcat", "ucs")),
}


@dataclass(frozen=True)
class Run:
    """One ingest run's rows in one stream of one table, with its ledger entry (status and
    started_at are None when the ledger row is missing)."""

    stream: str | None
    run_id: int
    status: str | None
    started_at: datetime | None
    rows: int


def runs_to_drop(
    runs: list[Run], prunable: tuple[str, ...] | None = None, keep_latest: int = KEEP_LATEST
) -> list[Run]:
    """The runs that the policy drops. Everything not returned is kept, so every doubt keeps.

    prunable: for a split table, the streams that may be pruned; None prunes every stream."""
    if keep_latest < 2:
        raise ValueError("keep_latest must be at least 2: identity/churn.py compares two runs")
    by_stream: dict[str | None, list[Run]] = {}
    for run in runs:
        by_stream.setdefault(run.stream, []).append(run)
    drop: list[Run] = []
    for stream, group in by_stream.items():
        if prunable is not None and stream not in prunable:
            continue
        ok = sorted((r for r in group if r.status == "ok"), key=lambda r: r.run_id)
        if not ok:
            continue  # no reader sees any of these, and nothing says which are safe to drop
        keep = {r.run_id for r in ok[-keep_latest:]}
        first_of_month: dict[str, int] = {}
        for r in ok:
            if r.started_at is not None:
                first_of_month.setdefault(r.started_at.astimezone(UTC).strftime("%Y-%m"), r.run_id)
        keep |= set(first_of_month.values())
        # Only runs OLDER than the newest OK one: a newer run is an ingest still in flight, which
        # no reader sees yet and which is not ours to judge.
        newest_ok = ok[-1].run_id
        drop += [r for r in group if r.run_id < newest_ok and r.run_id not in keep]
    return drop


def runs_in(conn, table: str, split: tuple[str, tuple[str, ...]] | None = None) -> list[Run]:
    """Every (stream, run) present in the table, with its row count and ledger entry."""
    stream = split[0] if split else "NULL::text"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT t.stream, t.ingest_run_id, i.status, i.started_at, t.n
            FROM (SELECT {stream} AS stream, ingest_run_id, count(*) AS n
                  FROM {table} GROUP BY 1, 2) t
            LEFT JOIN ingest_run i USING (ingest_run_id)
            ORDER BY 1, 2
            """
        )
        return [Run(*row) for row in cur.fetchall()]


def _dropped(split: tuple[str, tuple[str, ...]] | None, drop: list[Run]) -> tuple[str, dict]:
    """A predicate that is true for exactly the dropped rows, with its parameters. A split table
    matches (stream, run) pairs, so a run id that one stream drops and another keeps is exact."""
    params = {"runs": [r.run_id for r in drop], "streams": [r.stream for r in drop]}
    if split is None:
        return "ingest_run_id = ANY(%(runs)s)", params
    return (
        f"({split[0]}, ingest_run_id) IN "
        "(SELECT * FROM unnest(%(streams)s::text[], %(runs)s::bigint[]))",
        params,
    )


def delete_runs(
    conn, table: str, drop: list[Run], split: tuple[str, tuple[str, ...]] | None = None
) -> int:
    """DELETE the dropped runs' rows. The caller owns the transaction."""
    planned = sum(r.rows for r in drop)
    where, params = _dropped(split, drop)
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE {where}", params)
        if cur.rowcount != planned:
            raise RuntimeError(f"{table}: deleted {cur.rowcount} rows, planned {planned}")
    return planned


def compact(
    conn,
    table: str,
    runs: list[Run],
    drop: list[Run],
    split: tuple[str, tuple[str, ...]] | None = None,
) -> int:
    """Rewrite the table without the dropped runs' rows, so their space goes back to the
    operating system when the transaction commits. The caller owns the transaction and must hold
    an ACCESS EXCLUSIVE lock on the table from BEFORE it read `runs`: a row that landed between
    that read and the TRUNCATE would otherwise be lost."""
    planned = sum(r.rows for r in runs) - sum(r.rows for r in drop)
    where, params = _dropped(split, drop)
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
        # parameter. IS NOT TRUE keeps a row whose predicate is NULL: only a match is dropped.
        cur.execute(
            f"CREATE TEMP TABLE _prune_keep ON COMMIT DROP AS SELECT {cols} FROM {table} "
            "WITH NO DATA"
        )
        cur.execute(
            f"INSERT INTO _prune_keep SELECT {cols} FROM {table} WHERE ({where}) IS NOT TRUE",
            params,
        )
        kept = cur.rowcount
        if kept != planned:
            raise RuntimeError(f"{table}: would keep {kept} rows, planned {planned}")
        cur.execute(f"TRUNCATE {table}")
        # OVERRIDING SYSTEM VALUE: source_assertion.assertion_id and raw_supgp_status's id are
        # GENERATED ALWAYS identities, and a kept row keeps the id that it already has.
        cur.execute(
            f"INSERT INTO {table} ({cols}) OVERRIDING SYSTEM VALUE SELECT {cols} FROM _prune_keep"
        )
        if cur.rowcount != kept:
            raise RuntimeError(f"{table}: reinserted {cur.rowcount} rows of {kept}")
        cur.execute("DROP TABLE _prune_keep")
    return kept


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

    # Autocommit, so that each table gets its own explicit transaction below and VACUUM, which
    # cannot run inside one, can run after them.
    conn = get_autocommit_conn()
    touched: list[str] = []
    try:
        for table, split in SNAPSHOT_TABLES.items():
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
                if not cur.fetchone()[0]:
                    print(f"{table}: absent (migration not applied); skipped")
                    continue
                # Fail fast rather than queue behind a long transaction, and block everything
                # that queues behind this one.
                cur.execute("SET LOCAL lock_timeout = '10s'")
                if args.compact:
                    cur.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
                before = _mb(conn, table)
                runs = runs_in(conn, table, split)
                drop = runs_to_drop(runs, split[1] if split else None)
                dropped_rows = sum(r.rows for r in drop)
                total_rows = sum(r.rows for r in runs)
                plan = (
                    f"{table}: {len(runs)} runs, drop {len(drop)} "
                    f"({dropped_rows:,} of {total_rows:,} rows)"
                )
                if not drop:
                    print(f"{plan}; nothing to drop")
                    continue
                if args.compact:
                    compact(conn, table, runs, drop, split)
                    touched.append(table)
                    print(f"{plan}; compacted, {before:,.0f} MB -> {_mb(conn, table):,.0f} MB")
                elif args.apply:
                    delete_runs(conn, table, drop, split)
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
