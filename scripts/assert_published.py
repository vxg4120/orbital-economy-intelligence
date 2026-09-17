"""Pre-publish assertions for the served aggregates. Nightly, after the bus build and slug gate.

Exits 1 with every violation listed when a served number breaks an invariant a reader could
notice: ON-ORBIT above FLEET, a percentage outside 0..100, a cohort under the floor, a header
counter that disagrees with the table it summarises (quality/assertions.py has the list). The
refresh soft-fails into its log like every other step, so this line is what a morning reader
greps for. Read-only: it changes nothing, it only refuses to stay quiet.

Usage:
    .venv/bin/python scripts/assert_published.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.db import get_conn  # noqa: E402
from quality import assertions  # noqa: E402


def main() -> int:
    conn = get_conn()
    conn.read_only = True
    try:
        violations = assertions.run(conn)
    finally:
        conn.close()
    if violations:
        print(f"PUBLISH ASSERTIONS FAILED, {len(violations)} violations:")
        for v in violations[:60]:
            print(f"  {v}")
        if len(violations) > 60:
            print(f"  ... and {len(violations) - 60} more")
        return 1
    print(
        "PUBLISH ASSERTIONS PASSED: fleet >= on-orbit >= active on every served row, "
        "percentages within 0..100, cohort floor respected, header counters reconcile."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
