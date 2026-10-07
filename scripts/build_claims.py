"""Build the claim table from source_assertion's per-run history, once.

Replays every ingest run of every feed, in run order, through identity.claims.record: the
same writer the nightly uses, so the backfill and the nightly cannot disagree about what a
claim is. Resumable and idempotent: `record` refuses a run older than the last one recorded
and writes nothing for a run it has already seen, so an interrupted build is re-run as is.

source_assertion has no index on (source, ingest_run_id), and each replayed run must be read
by itself (35M rows, 96 runs). The build creates that index first, outside any transaction
(CONCURRENTLY, so readers never block), and drops it at the end unless --keep-index.

  (default)      dry run: list the runs that would be replayed and the index decision
  --apply        build
  --keep-index   leave the (source, ingest_run_id) index in place afterwards

Run on the box under nohup, outside the 07:10 and 19:10 UTC windows, with the laptop backup
of source_assertion taken first (docs/specs/assertion-model.md).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.db import get_autocommit_conn  # noqa: E402
from identity import claims  # noqa: E402

INDEX = "source_assertion_source_run_idx"


def runs_to_replay(conn) -> list[tuple[str, int]]:
    """(source, run) for every feed run in source_assertion, oldest first, from the last one the
    claim table already holds for that feed."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH done AS (
                SELECT source, greatest(max(first_run), max(closed_run)) AS run
                FROM claim GROUP BY source
            )
            SELECT a.source, a.ingest_run_id
            FROM (SELECT DISTINCT source, ingest_run_id FROM source_assertion) a
            LEFT JOIN done d ON d.source = a.source
            WHERE d.run IS NULL OR a.ingest_run_id >= d.run
            ORDER BY a.source, a.ingest_run_id
            """
        )
        return [(source, run) for source, run in cur.fetchall()]


def replay(conn, source: str, run: int) -> tuple[int, int]:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "SELECT source_key, attribute, value, max(observed_at) OVER () "
            "FROM source_assertion WHERE source = %s AND ingest_run_id = %s",
            (source, run),
        )
        rows = cur.fetchall()
        if not rows:
            return 0, 0
        observed_at = rows[0][3]
        return claims.record(conn, source, run, observed_at, [r[:3] for r in rows])


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--apply", action="store_true", help="build; the default is a dry run")
    ap.add_argument("--keep-index", action="store_true", help="keep the replay index afterwards")
    args = ap.parse_args()

    conn = get_autocommit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (INDEX,))
            has_index = cur.fetchone()[0]
        todo = runs_to_replay(conn)
        print(f"{len(todo)} runs to replay across {len({s for s, _ in todo})} feeds; "
              f"replay index {'present' if has_index else 'absent'}")
        if not args.apply:
            for source, run in todo[:5]:
                print(f"  would replay {source} run {run}")
            return 0
        if not has_index:
            print(f"creating {INDEX} concurrently")
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE INDEX CONCURRENTLY {INDEX} ON source_assertion (source, ingest_run_id)"
                )
        for source, run in todo:
            started = time.time()
            closed, opened = replay(conn, source, run)
            print(f"{source} run {run}: closed {closed:,}, opened {opened:,} "
                  f"in {time.time() - started:.1f}s", flush=True)
        if not args.keep_index:
            with conn.cursor() as cur:
                cur.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE closed_run IS NULL) FROM claim")
            total, open_ = cur.fetchone()
        print(f"build_claims: {total:,} claims, {open_:,} open")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
