"""Phase 2 gp_history backfill orchestrator (resumable, rate-limit-polite).

Backfills `gp_elements` from Space-Track `gp_history` for the benchmark operator fleet, using the
existing `ingest/spacetrack_client.py`. Work is a grid of (operator, monthly CREATION_DATE window),
each expanded into NORAD-id batches of 100 (one HTTP request each). The client's built-in 3s
throttle keeps us under Space-Track's <30/min cap; this orchestrator adds an *hourly* pacer so we
stay under their ~300/hr cap (target <=260/hr). Every completed (operator, window) unit is
checkpointed to `data/backfill_checkpoint.json`, so a restart skips finished work (landing is
idempotent anyway via the gp_elements PK — the checkpoint just saves requests).

Fleet selection (requirement 1): DISTINCT norad_id of PAYLOAD satellites owned (role='owner', any
validity — decayed birds count) by the benchmark operator OR any of its operator_relationship
children (this is how Eutelsat picks up the OneWeb fleet, and SpaceX picks up Swarm). NOTE on the
payload predicate: the identity resolver currently leaves `satellite.object_type='UNKNOWN'` for any
object carrying a GCAT type code (GCAT wins precedence but its space-padded 'P      O' codes don't
canonicalize), so a strict `satellite.object_type='PAYLOAD'` filter returns ~0 for SpaceX. We
therefore treat an object as a payload when the resolved column says PAYLOAD OR its authoritative
SATCAT object_type assertion is a 'PAY*' code — SATCAT being the object catalog of record. This is
a read-only fleet query; fixing the resolver is out of scope here.

Usage:
    python scripts/backfill_gp_history.py [--operators SpaceX,Capella] [--since 2025-07-01]
        [--until 2026-07-09] [--dry-run] [--reset] [--max-requests-per-hour 260]
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.db import get_conn  # noqa: E402
from ingest.spacetrack_client import SpaceTrackClient, land_gp_history  # noqa: E402

BATCH_SIZE = 100
DEFAULT_SINCE = dt.date(2025, 7, 1)
DEFAULT_MAX_RPH = 260
CHECKPOINT_PATH = REPO_ROOT / "data" / "backfill_checkpoint.json"

# Benchmark operators by their canonical name in the `operator` table (verified against the live
# DB / identity/operator_seed.yml). Order is the work order.
BENCHMARK_OPERATORS = [
    "SpaceX",
    "Eutelsat",  # includes the OneWeb fleet via operator_relationship (merged_into 2023-09-28)
    "Planet Labs",
    "Spire",
    "Iridium",
    "ICEYE",
    "Capella Space",
    "Amazon",  # Kuiper
]

# Friendly aliases accepted on the --operators flag, mapped to canonical names (case-insensitive).
_ALIAS_MAP = {
    "spacex": "SpaceX",
    "starlink": "SpaceX",
    "eutelsat": "Eutelsat",
    "oneweb": "Eutelsat",
    "planet": "Planet Labs",
    "planet labs": "Planet Labs",
    "spire": "Spire",
    "iridium": "Iridium",
    "iceye": "ICEYE",
    "capella": "Capella Space",
    "capella space": "Capella Space",
    "amazon": "Amazon",
    "kuiper": "Amazon",
}

# Fleet-selection SQL. `%(name)s` is the canonical operator name; the literal '%%' is an escaped
# LIKE wildcard (psycopg parameterisation). See module docstring for the payload predicate rationale.
FLEET_SQL = """
WITH target AS (
    SELECT operator_id FROM operator WHERE canonical_name = %(name)s
    UNION
    SELECT r.child_id
    FROM operator_relationship r
    JOIN operator p ON p.operator_id = r.parent_id
    WHERE p.canonical_name = %(name)s
)
SELECT DISTINCT s.norad_id
FROM satellite s
JOIN satellite_operator so ON so.satellite_id = s.satellite_id
WHERE so.role = 'owner'
  AND so.operator_id IN (SELECT operator_id FROM target)
  AND s.norad_id IS NOT NULL
  AND s.launch_date IS NOT NULL
  AND (
    s.object_type = 'PAYLOAD'
    OR EXISTS (
        SELECT 1 FROM source_assertion sa
        WHERE sa.satellite_id = s.satellite_id
          AND sa.attribute = 'object_type'
          AND upper(sa.value) LIKE 'PAY%%'
    )
  )
ORDER BY s.norad_id
"""


# --- fleet + windows ----------------------------------------------------------


def resolve_operators(tokens: list[str]) -> list[str]:
    """Map user-supplied operator tokens to canonical names, preserving order and de-duplicating."""
    out: list[str] = []
    for tok in tokens:
        key = tok.strip().casefold()
        canonical = _ALIAS_MAP.get(key)
        if canonical is None:
            raise ValueError(
                f"unknown operator {tok!r}; choose from {sorted(set(_ALIAS_MAP.values()))}"
            )
        if canonical not in out:
            out.append(canonical)
    return out


def verify_operators(conn, names: list[str]) -> None:
    """Fail loudly if any benchmark operator name is missing from the live `operator` table."""
    with conn.cursor() as cur:
        cur.execute("SELECT canonical_name FROM operator WHERE canonical_name = ANY(%s)", (names,))
        found = {r[0] for r in cur.fetchall()}
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"operators not found in operator table: {missing}")


def fleet_ids(conn, name: str) -> list[int]:
    """DISTINCT NORAD ids of the operator's (and its children's) on-orbit payload fleet."""
    with conn.cursor() as cur:
        cur.execute(FLEET_SQL, {"name": name})
        return [r[0] for r in cur.fetchall()]


def monthly_windows(since: dt.date, until: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Calendar-month [start, end) windows spanning [since, until). The final window is clipped
    to `until`. Empty if since >= until."""
    windows: list[tuple[dt.date, dt.date]] = []
    cur = since
    while cur < until:
        nxt = dt.date(cur.year + 1, 1, 1) if cur.month == 12 else dt.date(cur.year, cur.month + 1, 1)
        windows.append((cur, min(nxt, until)))
        cur = nxt
    return windows


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def make_plan(fleet_provider, operators: list[str], n_windows: int) -> list[dict]:
    """Per-operator work plan: ids, batch count, estimated requests (ceil(ids/100) x windows)."""
    plan = []
    for name in operators:
        ids = fleet_provider(name)
        n_batches = math.ceil(len(ids) / BATCH_SIZE)
        plan.append(
            {
                "operator": name,
                "ids": ids,
                "n_batches": n_batches,
                "est_requests": n_batches * n_windows,
            }
        )
    return plan


def format_plan(plan: list[dict], windows: list[tuple[dt.date, dt.date]]) -> str:
    lines = ["=== gp_history backfill plan ==="]
    if windows:
        lines.append(
            f"windows: {len(windows)} monthly "
            f"({windows[0][0].isoformat()} -> {windows[-1][1].isoformat()})"
        )
    else:
        lines.append("windows: 0 (empty date range)")
    lines.append(f"{'operator':<16}{'ids':>8}{'batches':>9}{'windows':>9}{'est.requests':>14}")
    total = 0
    total_ids = 0
    for p in plan:
        total += p["est_requests"]
        total_ids += len(p["ids"])
        lines.append(
            f"{p['operator']:<16}{len(p['ids']):>8}{p['n_batches']:>9}"
            f"{len(windows):>9}{p['est_requests']:>14}"
        )
    lines.append(f"{'TOTAL':<16}{total_ids:>8}{'':>9}{'':>9}{total:>14}")
    return "\n".join(lines)


# --- hourly pacer -------------------------------------------------------------


class HourlyPacer:
    """Sliding-window rate limiter: never lets more than `max_per_hour` requests start within any
    trailing 3600s window. Clock/sleep are injectable for testing."""

    WINDOW_S = 3600.0

    def __init__(self, max_per_hour: int, now=time.monotonic, sleep=time.sleep):
        self.max_per_hour = max_per_hour
        self._now = now
        self._sleep = sleep
        self._times: collections.deque[float] = collections.deque()

    def _prune(self, t: float) -> None:
        cutoff = t - self.WINDOW_S
        while self._times and self._times[0] <= cutoff:
            self._times.popleft()

    def acquire(self) -> float:
        """Block until a request slot is free, then record it. Returns seconds slept (0 if none)."""
        t = self._now()
        self._prune(t)
        slept = 0.0
        if len(self._times) >= self.max_per_hour:
            wait = self._times[0] + self.WINDOW_S - t
            if wait > 0:
                self._sleep(wait)
                slept = wait
                t = self._now()
                self._prune(t)
        self._times.append(t)
        return slept


# --- checkpoint ---------------------------------------------------------------


def unit_key(operator: str, window: tuple[dt.date, dt.date]) -> str:
    return f"{operator}|{window[0].isoformat()}|{window[1].isoformat()}"


class Checkpoint:
    """Records completed (operator, window) units to a JSON file, written atomically."""

    def __init__(self, path: Path = CHECKPOINT_PATH):
        self.path = Path(path)
        self.units: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with self.path.open() as fh:
                doc = json.load(fh)
            self.units = doc.get("units", {})

    def reset(self) -> None:
        self.units = {}
        if self.path.exists():
            self.path.unlink()

    def is_done(self, key: str) -> bool:
        return key in self.units

    def rows_done(self) -> int:
        return sum(u.get("rows", 0) for u in self.units.values())

    def mark_done(self, key: str, rows: int, requests: int) -> None:
        self.units[key] = {
            "rows": rows,
            "requests": requests,
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump({"version": 1, "units": self.units}, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# --- backfill runner ----------------------------------------------------------


def _fmt_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h:d}h{m:02d}m"


def run_backfill(
    conn,
    client,
    plan: list[dict],
    windows: list[tuple[dt.date, dt.date]],
    checkpoint: Checkpoint,
    *,
    land_fn=land_gp_history,
    max_requests_per_hour: int = DEFAULT_MAX_RPH,  # ETA estimation only; pacing is in the client
    stop_requested=lambda: False,
    out=print,
) -> dict:
    """Execute the plan unit-by-unit, pacing requests and checkpointing completed units.

    Returns a summary dict. Sets summary['stopped']=True if a stop was requested mid-run (the
    in-progress unit is left un-checkpointed so it restarts idempotently on resume)."""
    # Estimated requests remaining (skip already-checkpointed units) drives the ETA.
    est_total = sum(
        p["n_batches"]
        for p in plan
        for w in windows
        if not checkpoint.is_done(unit_key(p["operator"], w))
    )
    requests_done = 0
    cumulative_rows = checkpoint.rows_done()
    stopped = False

    for p in plan:
        operator, ids = p["operator"], p["ids"]
        for window in windows:
            key = unit_key(operator, window)
            if checkpoint.is_done(key):
                out(f"[skip] {key}  (checkpointed)")
                continue
            if stop_requested():
                stopped = True
                break

            since_s, before_s = window[0].isoformat(), window[1].isoformat()
            unit_rows = 0
            unit_requests = 0
            for batch in _chunk(ids, BATCH_SIZE):
                # Pacing happens inside the client (pre_request hook, per HTTP attempt incl.
                # retries) — see main(). No per-batch acquire here or requests double-count.
                rows = list(client.gp_history(batch, since_s, before_s))
                unit_rows += land_fn(conn, rows)
                unit_requests += 1
                requests_done += 1
                if stop_requested():
                    stopped = True
                    break

            if stopped:
                # Do not checkpoint a partially-processed unit; it restarts on resume.
                out(f"[stop] mid-unit {key} after {unit_requests} request(s); not checkpointed")
                break

            checkpoint.mark_done(key, rows=unit_rows, requests=unit_requests)
            cumulative_rows += unit_rows
            eta = _fmt_eta((est_total - requests_done) * (3600.0 / max_requests_per_hour))
            out(
                f"[done] {key}  req~{unit_requests}  rows+{unit_rows}  "
                f"cum_rows={cumulative_rows}  eta~{eta}"
            )
        if stopped:
            break

    return {
        "stopped": stopped,
        "requests_issued": requests_done,
        "units_completed": len(checkpoint.units),
        "cumulative_rows": cumulative_rows,
    }


# --- CLI ----------------------------------------------------------------------


def _parse_args(argv):
    today = dt.date.today()
    ap = argparse.ArgumentParser(description="Backfill gp_elements from Space-Track gp_history.")
    ap.add_argument(
        "--operators",
        default=",".join(BENCHMARK_OPERATORS),
        help="comma-separated operator names (aliases ok, e.g. Capella, Kuiper). Default: full set.",
    )
    ap.add_argument("--since", default=DEFAULT_SINCE.isoformat(), help="CREATION_DATE window start.")
    ap.add_argument("--until", default=today.isoformat(), help="CREATION_DATE window end (today).")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit.")
    ap.add_argument("--reset", action="store_true", help="clear the checkpoint before running.")
    ap.add_argument(
        "--max-requests-per-hour", type=int, default=DEFAULT_MAX_RPH, help="hourly request cap."
    )
    return ap.parse_args(argv)


def _resume_hint(args) -> str:
    parts = [
        "python scripts/backfill_gp_history.py",
        f"--operators {args.operators}",
        f"--since {args.since}",
        f"--until {args.until}",
    ]
    if args.max_requests_per_hour != DEFAULT_MAX_RPH:
        parts.append(f"--max-requests-per-hour {args.max_requests_per_hour}")
    return " ".join(parts)  # note: NO --reset, so completed units are skipped


def main(argv=None) -> int:
    args = _parse_args(argv)
    since = dt.date.fromisoformat(args.since)
    until = dt.date.fromisoformat(args.until)
    operators = resolve_operators(args.operators.split(","))
    windows = monthly_windows(since, until)

    conn = get_conn()
    try:
        verify_operators(conn, operators)
        plan = make_plan(lambda n: fleet_ids(conn, n), operators, len(windows))
        print(format_plan(plan, windows))

        if args.dry_run:
            print("[dry-run] plan only; no requests issued.")
            return 0
        if not windows:
            print("nothing to do: empty date range.")
            return 0

        checkpoint = Checkpoint()
        if args.reset:
            checkpoint.reset()
            print("[reset] checkpoint cleared.")

        stop = {"flag": False}

        def _handle_sigint(signum, frame):
            stop["flag"] = True
            print("\n[signal] SIGINT received; finishing current batch's landing, then stopping...")

        signal.signal(signal.SIGINT, _handle_sigint)

        # The pacer hooks the client's pre-request callback so RETRIES count against the hourly
        # budget too — pacing only the per-batch call undercounts during retry storms (observed
        # live: ~450/hr actual vs the 260/hr per-call budget, which tripped Space-Track's
        # HTTP-200 rate-limit stubs and silently emptied whole windows).
        pacer = HourlyPacer(args.max_requests_per_hour)
        client = SpaceTrackClient(conn, pre_request=pacer.acquire)
        try:
            summary = run_backfill(
                conn,
                client,
                plan,
                windows,
                checkpoint,
                max_requests_per_hour=args.max_requests_per_hour,
                stop_requested=lambda: stop["flag"],
            )
        except Exception as exc:  # noqa: BLE001 - top-level: checkpoint is safe, report + exit
            checkpoint.save()
            print(f"\n[error] {type(exc).__name__}: {exc}", file=sys.stderr)
            print(f"[resume] {_resume_hint(args)}", file=sys.stderr)
            return 1

        print(
            f"=== backfill {'stopped' if summary['stopped'] else 'complete'} ===\n"
            f"units completed: {summary['units_completed']}  "
            f"requests this run: {summary['requests_issued']}  "
            f"cumulative rows: {summary['cumulative_rows']}"
        )
        if summary["stopped"]:
            print(f"[resume] {_resume_hint(args)}", file=sys.stderr)
            return 130
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
