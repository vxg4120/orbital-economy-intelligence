"""Refresh the derived materialized views. Nightly, after GP ingest, before the report.

Two views, one job each:

  mv_latest_gp_element   the newest element set per satellite (migration 0019). Nine
                         consumers used to re-derive this with a DISTINCT ON over the whole
                         10.2M-row gp_elements hypertable per query; now they read 17k rows.
                         GP data only changes at ingest time, so refreshing right after
                         ingest makes the materialization identically fresh to a live query.
  mv_drag_daily          the fleet-wide daily drag series (metrics/space_weather.sql), whose
                         pair-building window scan costs seconds nobody should pay on a page
                         load.

CONCURRENTLY so API reads never block mid-refresh (both views carry the unique index that
makes that legal). Each view is guarded on existence, so a database that has not applied the
migration or metrics yet skips cleanly instead of tracebacking the nightly log.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.db import get_autocommit_conn  # noqa: E402

MATVIEWS = ["mv_latest_gp_element", "mv_drag_daily"]


def main() -> int:
    conn = get_autocommit_conn()
    try:
        with conn.cursor() as cur:
            for mv in MATVIEWS:
                cur.execute("SELECT to_regclass(%s) IS NOT NULL", (mv,))
                if not cur.fetchone()[0]:
                    print(f"{mv}: absent (migrations/metrics not applied); skipped")
                    continue
                cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}")
                cur.execute(f"SELECT count(*) FROM {mv}")
                print(f"{mv}: refreshed, {cur.fetchone()[0]} rows")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
