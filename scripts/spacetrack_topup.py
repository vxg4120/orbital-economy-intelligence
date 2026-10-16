#!/usr/bin/env python
"""Nightly Space-Track top-up: new gp_history element sets, then actual decays.

Runs in deploy/nightly-refresh.sh after ingest_all and before build_graph. Without
SPACETRACK_IDENTITY / SPACETRACK_PASSWORD it prints one line and exits 0, making no request.
See docs/specs/spacetrack-topup.md.

gp_history: the benchmark fleet of scripts/backfill_gp_history.py (same fleet query, same
client, same landing into gp_elements with source='spacetrack_gp_history' and its ON CONFLICT
DO NOTHING key), asked only for element sets whose CREATION_DATE falls after the last
successful night's upper bound (spacetrack_cursor) and up to now. Space-Track allows
gp_history "once per lifetime" for the same data, so windows never overlap; the cursor
advances only when every batch landed. A gap longer than MAX_GAP_DAYS is refused with the
backfill command to fill it, so a long outage can never turn into one giant nightly query.

decay: ingest/spacetrack_decay.py, one query a day.

Rate limits (Space-Track: <30 requests/minute, <300/hour): the client spaces requests 3 s
apart (<=20/minute) and an HourlyPacer on the client's per-attempt hook caps the run at
MAX_REQUESTS_PER_HOUR, retries included. A plan larger than MAX_PLANNED_REQUESTS is refused
before any request, so the pacer never has to put the nightly to sleep for an hour.

Usage: python scripts/spacetrack_topup.py [--only gp|decay] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.db import get_conn  # noqa: E402
from ingest import runlog, spacetrack_decay  # noqa: E402
from ingest.spacetrack_client import SpaceTrackClient, land_gp_history  # noqa: E402
from scripts import backfill_gp_history as bf  # noqa: E402

SOURCE = "spacetrack"
TOPUP_ENDPOINT = "gp_history_topup"
CURSOR_STREAM = "gp_history_topup"
# Once a day is enough: windows are incremental, so a second slot would only split the same
# data into twice the requests.
TOPUP_MIN_INTERVAL = runlog.DAILY
# First night with no cursor: start this far back (the gap before it is a one-off backfill).
INITIAL_LOOKBACK = dt.timedelta(days=3)
MAX_GAP_DAYS = 30
# The window ends this long before now. An element set can become queryable some time after
# its CREATION_DATE (it is stamped when built, published after), and a window that ended at
# "now" would put such a set behind the cursor before it was visible: skipped for good.
PUBLICATION_MARGIN = dt.timedelta(hours=2)
# A fleet this large returning no element set at all over a whole window is not a quiet night,
# it is a broken query (a throttle stub that slipped through, a changed field, a bad range).
EMPTY_WINDOW_MIN_IDS = 100
MAX_REQUESTS_PER_HOUR = 250   # Space-Track's cap is 300; headroom for login and the decay pull
MAX_PLANNED_REQUESTS = 200    # refuse a plan this large: retries need room under the hourly cap


class TopupGapError(RuntimeError):
    """The cursor is too old for a nightly window; the gap needs a deliberate backfill."""


class TopupBudgetError(RuntimeError):
    """The fleet would take more requests than one nightly may spend."""


class TopupEmptyError(RuntimeError):
    """The window cannot be trusted as done: no fleet, or a large fleet that returned nothing."""


class _MemoryCheckpoint:
    """run_backfill's checkpoint interface without the file: the cursor is the checkpoint."""

    def __init__(self):
        self.units: dict[str, dict] = {}

    def is_done(self, key):
        return key in self.units

    def rows_done(self):
        return sum(u["rows"] for u in self.units.values())

    def mark_done(self, key, rows, requests):
        self.units[key] = {"rows": rows, "requests": requests}

    def save(self):
        pass


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def read_cursor(conn) -> dt.datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT through FROM spacetrack_cursor WHERE stream = %s", (CURSOR_STREAM,))
        row = cur.fetchone()
    return row[0] if row else None


def _write_cursor(conn, through: dt.datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO spacetrack_cursor (stream, through, updated_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (stream) DO UPDATE SET through = EXCLUDED.through, updated_at = now()",
            (CURSOR_STREAM, through),
        )


def _naive_utc_seconds(t: dt.datetime) -> dt.datetime:
    """Space-Track's CREATION_DATE is UTC without an offset: 2026-10-16T07:20:00."""
    return t.astimezone(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def topup_window(conn, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """(created_since, created_through) for tonight, both naive UTC to the second. The window
    ends PUBLICATION_MARGIN before now."""
    through = read_cursor(conn)
    since = through if through is not None else now - INITIAL_LOOKBACK
    upper = now - PUBLICATION_MARGIN
    if upper - since > dt.timedelta(days=MAX_GAP_DAYS):
        raise TopupGapError(
            f"gp_history cursor is at {since:%Y-%m-%dT%H:%M:%SZ}, more than {MAX_GAP_DAYS} days "
            "back. Fill the gap deliberately, outside the nightly windows, then set the cursor: "
            f"python scripts/backfill_gp_history.py --since {since:%Y-%m-%d} "
            f"--until {now:%Y-%m-%d}; UPDATE spacetrack_cursor SET through = "
            f"'{now:%Y-%m-%d} 00:00:00+00' WHERE stream = '{CURSOR_STREAM}'"
        )
    return _naive_utc_seconds(since), _naive_utc_seconds(upper)


def _fail_empty(conn, run_id: int, message: str):
    runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="error", notes=message[:2000])
    raise TopupEmptyError(message)


def gp_topup(conn, client, *, now=None, fleet_provider=None, operators=None,
             land_fn=land_gp_history, dry_run=False, out=print) -> int:
    """Pull and land tonight's gp_history window. Returns rows submitted (0 when skipped)."""
    now = now or _utc_now()
    operators = operators or bf.BENCHMARK_OPERATORS
    if not dry_run and runlog.fresh_within(conn, SOURCE, TOPUP_ENDPOINT, TOPUP_MIN_INTERVAL):
        run_id = runlog.start_run(conn, SOURCE, TOPUP_ENDPOINT)
        runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="skipped_fresh")
        out(f"[spacetrack_topup] gp_history skipped: an OK top-up finished within "
            f"{TOPUP_MIN_INTERVAL}")
        return 0

    since, through = topup_window(conn, now)
    if through <= since:
        out(f"[spacetrack_topup] gp_history skipped: empty window ({since.isoformat()} is not "
            f"before {through.isoformat()})")
        return 0
    if fleet_provider is None:
        decayed_after = since.date() - dt.timedelta(days=1)
        bf.verify_operators(conn, operators)

        def fleet_provider(name):
            return bf.fleet_ids(conn, name, decayed_after=decayed_after)

    windows = [(since, through)]
    plan = bf.make_plan(fleet_provider, operators, n_windows=1)
    planned = sum(p["est_requests"] for p in plan)
    out(f"[spacetrack_topup] gp_history CREATION_DATE {since.isoformat()}--{through.isoformat()}"
        f": {sum(len(p['ids']) for p in plan)} ids, {planned} request(s)")
    if dry_run:
        return 0
    if planned > MAX_PLANNED_REQUESTS:
        raise TopupBudgetError(
            f"{planned} requests planned, more than the nightly's {MAX_PLANNED_REQUESTS}; "
            "narrow the fleet or raise the budget deliberately (docs/specs/spacetrack-topup.md)"
        )

    run_id = runlog.start_run(conn, SOURCE, TOPUP_ENDPOINT)
    window_note = f"CREATION_DATE {since.isoformat()}--{through.isoformat()}"
    n_ids = sum(len(p["ids"]) for p in plan)
    if n_ids == 0:
        _fail_empty(conn, run_id, f"{window_note}: the fleet query returned no NORAD ids; "
                    "cursor not advanced")
    try:
        summary = bf.run_backfill(conn, client, plan, windows, _MemoryCheckpoint(),
                                  land_fn=land_fn, max_requests_per_hour=MAX_REQUESTS_PER_HOUR,
                                  out=out)
    except Exception as exc:
        conn.rollback()
        runlog.finish_run(conn, run_id, rows=0, bytes_dl=0, status="error",
                          notes=f"{window_note}: {exc}"[:2000])
        raise
    if summary["cumulative_rows"] == 0 and n_ids >= EMPTY_WINDOW_MIN_IDS:
        _fail_empty(conn, run_id, f"{window_note}: {n_ids} ids in "
                    f"{summary['requests_issued']} request(s) returned no element set; "
                    "cursor not advanced")
    # The cursor and the ledger's 'ok' commit together: either tonight counts or it does not.
    _write_cursor(conn, through.replace(tzinfo=dt.timezone.utc))
    runlog.finish_run(conn, run_id, rows=summary["cumulative_rows"], bytes_dl=0, status="ok",
                      notes=f"{window_note}, {summary['requests_issued']} request(s)")
    out(f"[spacetrack_topup] gp_history landed {summary['cumulative_rows']} row(s) in "
        f"{summary['requests_issued']} request(s); cursor -> {through.isoformat()}")
    return summary["cumulative_rows"]


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="Nightly Space-Track top-up (gp_history + decay).")
    ap.add_argument("--only", choices=["gp", "decay"], help="run one part only")
    ap.add_argument("--dry-run", action="store_true",
                    help="print tonight's gp_history window and plan; no requests")
    return ap.parse_args(argv)


def main(argv=None, conn=None, client_factory=None, now=None, out=print) -> int:
    args = _parse_args(argv)
    identity = os.environ.get("SPACETRACK_IDENTITY", "").strip()
    password = os.environ.get("SPACETRACK_PASSWORD", "").strip()
    if not identity or not password:
        out("[spacetrack_topup] SPACETRACK_IDENTITY/SPACETRACK_PASSWORD not set; skipping, "
            "no requests made")
        return 0

    owns_conn = conn is None
    conn = conn or get_conn()
    failed: list[str] = []
    try:
        pacer = bf.HourlyPacer(MAX_REQUESTS_PER_HOUR)
        factory = client_factory or (lambda c, **kw: SpaceTrackClient(c, **kw))
        client = factory(conn, identity=identity, password=password, pre_request=pacer.acquire)
        parts = [args.only] if args.only else ["gp", "decay"]
        if args.dry_run:
            parts = [p for p in parts if p == "gp"]
        for part in parts:
            try:
                if part == "gp":
                    gp_topup(conn, client, now=now, dry_run=args.dry_run, out=out)
                else:
                    spacetrack_decay.run(conn, client, out=out)
            except Exception as exc:  # one part failing must not stop the other
                conn.rollback()
                print(f"[spacetrack_topup] {part} failed: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                failed.append(part)
    finally:
        if owns_conn:
            conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
