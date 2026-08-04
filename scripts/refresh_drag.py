"""Refresh mv_drag_daily (the fleet drag series). Nightly, after GP ingest, before the report.

The materialization exists because the pair-building window scan over sat_daily costs seconds
nobody should pay on a page load; this script is where that cost is paid instead. CONCURRENTLY
so API reads never block on the refresh (the unique index metrics/space_weather.sql creates is
what makes that legal). Guarded on the matview existing, so a database that has not applied
metrics yet exits cleanly instead of tracebacking the nightly log.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.db import get_autocommit_conn  # noqa: E402


def main() -> int:
    conn = get_autocommit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('mv_drag_daily') IS NOT NULL")
            if not cur.fetchone()[0]:
                print("mv_drag_daily absent (run scripts/apply_metrics.py first); nothing to do")
                return 0
            cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_drag_daily")
            cur.execute("SELECT count(*), max(day) FROM mv_drag_daily")
            days, latest = cur.fetchone()
    finally:
        conn.close()
    print(f"mv_drag_daily refreshed: {days} days, latest {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
