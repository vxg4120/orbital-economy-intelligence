"""mv_latest_satellite_status: the materialization behind the buses leaderboard's status join.

What must never drift: the view IS "the newest canonical_status per satellite" (never ahead of
the live history, never fabricated, one row per satellite), its tiebreak is the total order
(observed_at DESC, then source), and the three metrics views that switched to it read it rather
than the live table. The refresh path is exercised for real, because a broken refresh would
freeze the leaderboard's fleet and on-orbit counts at yesterday's state with no error anywhere.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_CONSUMERS = ("v_bus_sat", "v_station_keeping_30d", "v_deorbit_compliance")


@pytest.mark.db
@pytest.mark.graph
def test_view_is_exactly_one_latest_row_per_satellite(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM mv_latest_satellite_status), "
            "(SELECT count(DISTINCT satellite_id) FROM satellite_status_history)"
        )
        mv_rows, live_sats = cur.fetchone()
        # A snapshot refreshed by its own nightly step: it may lag the live table after a
        # build_graph lands, but it must never lead it and must never go vacuous.
        assert mv_rows <= live_sats, "the matview holds satellites the live history does not"
        assert mv_rows > 15000, "matview went vacuous; check scripts/refresh_matviews.py"

        cur.execute(
            "SELECT count(*), count(DISTINCT satellite_id) FROM mv_latest_satellite_status"
        )
        rows, distinct = cur.fetchone()
        assert rows == distinct, "a satellite appears twice in the matview"

        cur.execute(
            """
            SELECT count(*) FROM mv_latest_satellite_status mv
            JOIN (SELECT satellite_id, max(observed_at) AS max_at
                  FROM satellite_status_history GROUP BY 1) t USING (satellite_id)
            WHERE mv.observed_at > t.max_at
            """
        )
        assert cur.fetchone()[0] == 0, "the matview stores a status newer than any real row"
        # Scoped to satellites that still have history: a merge between the refresh and this
        # assertion repoints or deletes the merged satellite's rows (identity/merge.py), and the
        # snapshot keeping that satellite until the next refresh is lag, not fabrication.
        cur.execute(
            """
            SELECT count(*) FROM mv_latest_satellite_status mv
            WHERE EXISTS (SELECT 1 FROM satellite_status_history h
                          WHERE h.satellite_id = mv.satellite_id)
              AND NOT EXISTS (
                SELECT 1 FROM satellite_status_history h
                WHERE h.satellite_id = mv.satellite_id AND h.observed_at = mv.observed_at
                  AND h.source = mv.source AND h.canonical_status = mv.canonical_status
            )
            """
        )
        assert cur.fetchone()[0] == 0, "the matview stores a status row that does not exist"


@pytest.mark.db
def test_tiebreak_is_the_total_order(db_conn):
    """Equal observed_at rows from different sources are resolved by source, the same way on
    every refresh, so a consumer never sees a satellite flip status between two refreshes
    that saw identical data. Scoped to the rows the snapshot saw (observed_at no newer than
    the stored one) so a row landing between refresh and assertion is lag, not a failure."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM mv_latest_satellite_status mv
            JOIN satellite_status_history h USING (satellite_id, observed_at)
            WHERE h.source < mv.source
              AND h.observed_at <= mv.observed_at
            """
        )
        assert cur.fetchone()[0] == 0, "a tie was not resolved by the lowest source"


def test_alphabetical_source_tiebreak_matches_status_precedence():
    """The matview breaks an observed_at tie by source name ascending, which today equals the
    status precedence in identity/precedence.yml (gcat, satcat, ucs) only because those names
    happen to sort that way. This pins the coincidence so a new status source that would
    invert it fails here instead of silently outranking GCAT on tied timestamps."""
    import yaml

    order = yaml.safe_load((REPO_ROOT / "identity" / "precedence.yml").read_text())["status"]
    assert order == sorted(order), (
        f"status precedence {order} no longer sorts alphabetically; change the matview's "
        "tiebreak in db/migrations (a new migration) to follow precedence explicitly"
    )


@pytest.mark.db
def test_consumers_read_the_matview_not_the_live_table(db_conn):
    """The three views that used to DISTINCT ON over the whole history now depend on the
    matview. Checked through pg_depend so a CREATE OR REPLACE that quietly reverts one of
    them to the live scan fails here, not on the next 4-second page load."""
    with db_conn.cursor() as cur:
        for view in _CONSUMERS:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_depend d
                    JOIN pg_rewrite r ON r.oid = d.objid
                    WHERE r.ev_class = %s::regclass
                      AND d.refobjid = 'mv_latest_satellite_status'::regclass
                )
                """,
                (view,),
            )
            assert cur.fetchone()[0], f"{view} no longer reads mv_latest_satellite_status"


@pytest.mark.db
def test_refresh_script_refreshes_the_status_view(db_conn):
    """scripts/refresh_matviews.py is the only thing that moves this view; run it for real."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "refresh_matviews.py")],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "mv_latest_satellite_status: refreshed" in proc.stdout, proc.stdout
