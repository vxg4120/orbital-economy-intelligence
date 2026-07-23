"""Bus Benchmarks build CLI.

Rebuilds satellite_bus from the latest OK GCAT snapshot (identity/bus.py), extracts the
'bus' / 'manufacturer' source assertions (identity/assertions.py, idempotent), refreshes the
behavior materialized view backing the benchmark views, and freezes the current month's
leaderboard snapshot (first run of each month wins; later runs insert nothing).

Runs after scripts/build_graph.py in the daily cycle: attribution joins through the
satellite_identifier crosswalk the graph build maintains. Safe to re-run at any time.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.db import get_conn  # noqa: E402
from identity import assertions, bus  # noqa: E402


def main() -> None:
    conn = get_conn()
    try:
        stats = bus.build(conn)
        assertions.extract(conn)
        refreshed = bus.refresh_behavior_matview(conn)
        snapshots = bus.snapshot_benchmarks(conn)
        conn.commit()
    finally:
        conn.close()

    print("=== bus attribution build summary ===")
    print(f"attributed satellites:   {stats['attributed']}")
    print(f"  with bus model:        {stats['with_bus']}")
    print(f"  with manufacturer:     {stats['with_manufacturer']}")
    print(f"distinct bus models:     {stats['bus_models']}")
    print(f"distinct manufacturers:  {stats['manufacturers']}")
    print(
        "parent rollups:          "
        f"{stats['rolled_up']} via gcat_orgs, {stats['rolled_up_override']} via override, "
        f"{stats['unresolved_codes']} unresolved codes"
    )
    print(f"behavior matview:        {'refreshed' if refreshed else 'absent (run make metrics)'}")
    for kind, n in snapshots.items():
        state = "views absent" if n is None else f"{n} rows inserted"
        print(f"snapshot [{kind}]:        {state}")


if __name__ == "__main__":
    main()
