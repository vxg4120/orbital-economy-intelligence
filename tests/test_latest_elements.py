"""mv_latest_gp_element: the one materialization behind nine former whole-table scans.

What must never drift: the view IS "the newest element per satellite" (cardinality and
max-epoch equality against the live table), its tiebreak is the total order every consumer
now inherits (epoch DESC, then source), and the consumers that switched to it still serve
the same answers. The refresh path is exercised for real, because a broken refresh would
freeze every one of those surfaces at yesterday's sky with no error anywhere.
"""

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


@pytest.mark.db
def test_view_is_exactly_one_latest_row_per_satellite(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM mv_latest_gp_element), "
            "(SELECT count(DISTINCT norad_id) FROM gp_elements)"
        )
        mv_rows, live_norads = cur.fetchone()
        assert mv_rows == live_norads, "cardinality drifted from the live table"
        # Every stored epoch is the satellite's true maximum.
        cur.execute(
            """
            SELECT count(*) FROM mv_latest_gp_element mv
            JOIN (SELECT norad_id, max(epoch) AS max_epoch FROM gp_elements GROUP BY 1) t
              ON t.norad_id = mv.norad_id
            WHERE mv.epoch <> t.max_epoch
            """
        )
        assert cur.fetchone()[0] == 0, "a stale epoch is stored as latest"


@pytest.mark.db
def test_tiebreak_is_the_total_order(db_conn):
    """Equal-epoch elements from different sources exist in the wild (observed: celestrak_gp
    and spacetrack_gp_history at the identical epoch); the view must resolve them by source
    ascending, deterministically, everywhere."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM mv_latest_gp_element mv
            WHERE EXISTS (
                SELECT 1 FROM gp_elements g
                WHERE g.norad_id = mv.norad_id AND g.epoch = mv.epoch
                  AND g.source < mv.source
            )
            """
        )
        assert cur.fetchone()[0] == 0, "a tie was resolved against the declared order"


@pytest.mark.db
def test_congestion_bins_agree_with_the_view(db_conn):
    client = _client()
    bins = client.get("/api/congestion").json()["bins"]
    total = sum(b["object_count"] for b in bins)
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM mv_latest_gp_element "
            "WHERE inclination IS NOT NULL AND perigee_km IS NOT NULL "
            "  AND apogee_km IS NOT NULL AND ((apogee_km + perigee_km) / 2.0) < 2000"
        )
        expected = cur.fetchone()[0]
    assert total == expected


@pytest.mark.db
def test_refresh_script_runs_and_reports_both_views():
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "refresh_matviews.py")],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "mv_latest_gp_element: refreshed" in proc.stdout
    assert "mv_drag_daily" in proc.stdout


def test_deprecated_shim_delegates():
    """scripts/ is baked into the docker image while deploy/ is bind-mounted, so a pulled
    nightly script can call into an older image; the shim keeps that window safe."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "refresh_drag_shim", REPO_ROOT / "scripts" / "refresh_drag.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(REPO_ROOT / "scripts"))
    from scripts.refresh_matviews import main as real_main  # noqa: PLC0415

    assert module.main is real_main or module.main.__module__ == "refresh_matviews"
