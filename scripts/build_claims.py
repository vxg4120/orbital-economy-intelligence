"""Build the claim table from source_assertion's per-run history, once.

Replays every ingest run of every snapshot feed, in run order, through identity.claims.record:
the same writer the nightly uses, so the backfill and the nightly cannot disagree about what a
claim is. The feed's advisory lock is held for the whole pass, so a nightly that fires
meanwhile waits rather than recording its newer run first and leaving the rest of the history
refused. Resumable: a feed's progress row says where to continue, and a run at or before it is
refused by the writer.

source_assertion has no index on (source, ingest_run_id), and each replayed run must be read
by itself (35M rows, 96 runs). The build creates that index first, outside any transaction
(CONCURRENTLY, so readers never block; a half-built one from an interruption is dropped and
rebuilt), replays one run at a time, and drops the index at the end unless --keep-index.

  (default)      dry run: the runs that would be replayed, per feed, and the index decision
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


def runs_to_replay(conn, source: str) -> list[int]:
    """The feed's runs in source_assertion after its progress row, oldest first."""
    with conn.cursor() as cur:
        before = claims.progress(cur, source)
        cur.execute(
            "SELECT DISTINCT ingest_run_id FROM source_assertion "
            "WHERE source = %s AND ingest_run_id > %s ORDER BY 1",
            (source, before[0] if before else 0),
        )
        return [run for (run,) in cur.fetchall()]


def replay(conn, source: str, run: int) -> tuple[int, int]:
    """One run, streamed; the writer sees it as a bootstrap run of the feed."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT max(observed_at) FROM source_assertion "
                "WHERE source = %s AND ingest_run_id = %s",
                (source, run),
            )
            observed_at = cur.fetchone()[0]
        # One run at a time (about 700k small tuples, around 100 MB): record() materializes
        # its rows before the COPY, since a COPY cannot interleave with a server-side cursor.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_key, attribute, value FROM source_assertion "
                "WHERE source = %s AND ingest_run_id = %s",
                (source, run),
            )
            rows = cur.fetchall()
        return claims.record(conn, source, run, observed_at, rows, bootstrap=True)


def _index_state(cur) -> str:
    cur.execute(
        "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = %s",
        (INDEX,),
    )
    row = cur.fetchone()
    return "absent" if row is None else ("valid" if row[0] else "invalid")


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
            cur.execute("SET temp_file_limit = '2GB'")
            state = _index_state(cur)
        todo = {source: runs_to_replay(conn, source) for source in claims.SOURCES}
        for source, runs in todo.items():
            print(f"{source}: {len(runs)} runs to replay"
                  + (f", {runs[0]}..{runs[-1]}" if runs else ""))
        print(f"replay index {state}")
        if not args.apply:
            return 0
        with conn.cursor() as cur:
            if state == "invalid":
                cur.execute(f"DROP INDEX CONCURRENTLY {INDEX}")
                state = "absent"
            if state == "absent":
                print(f"creating {INDEX} concurrently", flush=True)
                cur.execute(
                    f"CREATE INDEX CONCURRENTLY {INDEX} ON source_assertion (source, ingest_run_id)"
                )
        for source, runs in todo.items():
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(hashtext('claim:' || %s))", (source,))
            try:
                for run in runs:
                    started = time.time()
                    closed, opened = replay(conn, source, run)
                    print(f"{source} run {run}: closed {closed:,}, opened {opened:,} "
                          f"in {time.time() - started:.1f}s", flush=True)
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(hashtext('claim:' || %s))", (source,))
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
