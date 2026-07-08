"""Tests for metrics/caggs.sql + metrics/benchmark_views.sql (Task 5).

All DB-backed. Synthetic rows use norad ids in the reserved test range
920000001-929999999 and are inserted inside db_conn's transaction, which every test rolls back
in a finally block -- nothing is ever committed to the shared dev DB.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APPLY_SCRIPT = REPO_ROOT / "scripts" / "apply_metrics.py"

# Reserved synthetic norad-id range for this task's db tests.
SAT_A = 920100001
SAT_B = 920100002

OP_ALPHA = "ZZ TEST OPERATOR ALPHA (test_metrics)"
OP_BETA = "ZZ TEST OPERATOR BETA (test_metrics)"

DAY_1 = "2026-06-01"
DAY_2 = "2026-06-02"
DAY_3 = "2026-06-03"  # acquisition takes effect this day


def _run_apply_metrics():
    return subprocess.run(
        [sys.executable, str(APPLY_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.db
def test_apply_metrics_is_idempotent(db_conn):
    """apply_metrics.py applies caggs + views cleanly, and re-running is a no-op success."""
    first = _run_apply_metrics()
    assert "applied caggs.sql" in first.stdout
    assert "applied benchmark_views.sql" in first.stdout

    second = _run_apply_metrics()
    assert "applied caggs.sql" in second.stdout
    assert "applied benchmark_views.sql" in second.stdout

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT materialized_only FROM timescaledb_information.continuous_aggregates "
            "WHERE view_name = 'sat_daily'"
        )
        row = cur.fetchone()
    assert row is not None, "sat_daily continuous aggregate was not created"
    assert row[0] is False, "sat_daily must have materialized_only=false (real-time aggregation)"


def _seed_acquisition_fixture(cur):
    """Two fake satellites, one operator acquisition mid-window, 3 days each of GP data.

    SAT_A: owned by OP_ALPHA on DAY_1/DAY_2, transfers to OP_BETA on DAY_3 (the acquisition).
    SAT_B: owned by OP_ALPHA throughout, only DAY_1 data (also feeds the congestion-bin check
    with a second, differently-inclined object).
    """
    cur.execute(
        "INSERT INTO satellite (norad_id, cospar_id, canonical_name, object_type, launch_date) "
        "VALUES (%s, '2020-001A', 'ZZ TEST SAT A', 'PAYLOAD', '2020-01-01'), "
        "       (%s, '2020-002A', 'ZZ TEST SAT B', 'PAYLOAD', '2020-01-01') "
        "RETURNING norad_id, satellite_id",
        (SAT_A, SAT_B),
    )
    sat_ids = dict(cur.fetchall())

    cur.execute(
        "INSERT INTO operator (canonical_name, country, operator_class) "
        "VALUES (%s, 'US', 'commercial'), (%s, 'US', 'commercial') "
        "RETURNING canonical_name, operator_id",
        (OP_ALPHA, OP_BETA),
    )
    op_ids = dict(cur.fetchall())

    # Adjacent SCD2 rows that SHARE the split boundary (ALPHA.valid_to == BETA.valid_from == DAY_3),
    # exactly as identity/resolve.py writes an acquisition split. Under the (buggy) closed BETWEEN
    # DAY_3 matched BOTH rows (double-count); under the half-open [valid_from, valid_to) join it
    # belongs to exactly one operator -- the incoming BETA.
    cur.execute(
        "INSERT INTO satellite_operator "
        "(satellite_id, operator_id, role, valid_from, valid_to, source) VALUES "
        "(%s, %s, 'owner', %s, %s, 'test'), "  # SAT_A -> ALPHA, pre-acquisition [DAY_1, DAY_3)
        "(%s, %s, 'owner', %s, NULL, 'test'), "  # SAT_A -> BETA, post-acquisition [DAY_3, NULL)
        "(%s, %s, 'owner', %s, NULL, 'test')",  # SAT_B -> ALPHA, unchanged
        (
            sat_ids[SAT_A], op_ids[OP_ALPHA], DAY_1, DAY_3,
            sat_ids[SAT_A], op_ids[OP_BETA], DAY_3,
            sat_ids[SAT_B], op_ids[OP_ALPHA], DAY_1,
        ),
    )

    cur.execute(
        "INSERT INTO gp_elements "
        "(norad_id, epoch, mean_motion, eccentricity, inclination, source) VALUES "
        "(%s, %s, 15.5, 0.0004, 53.0, 'test'), "
        "(%s, %s, 15.5, 0.0004, 53.0, 'test'), "
        "(%s, %s, 15.5, 0.0004, 53.0, 'test'), "
        "(%s, %s, 14.2, 0.0010, 97.5, 'test')",
        (
            SAT_A, f"{DAY_1}T00:00:00Z",
            SAT_A, f"{DAY_2}T00:00:00Z",
            SAT_A, f"{DAY_3}T00:00:00Z",
            SAT_B, f"{DAY_1}T00:00:00Z",
        ),
    )
    return sat_ids, op_ids


@pytest.mark.db
def test_v_sat_operator_daily_attributes_days_by_ownership_window(db_conn):
    """The killer-chart mechanic: physics (sat_daily) is fixed, attribution flips at the
    acquisition date. Same object, same orbit, different operator before/after DAY_3."""
    try:
        with db_conn.cursor() as cur:
            _seed_acquisition_fixture(cur)

            cur.execute(
                "SELECT day::date, operator_name FROM v_sat_operator_daily "
                "WHERE norad_id = %s ORDER BY day",
                (SAT_A,),
            )
            rows = cur.fetchall()

        assert [r[0].isoformat() for r in rows] == [DAY_1, DAY_2, DAY_3]
        by_day = {r[0].isoformat(): r[1] for r in rows}
        assert by_day[DAY_1] == OP_ALPHA
        assert by_day[DAY_2] == OP_ALPHA
        assert by_day[DAY_3] == OP_BETA
        # Same underlying orbit throughout -- the attribution change is purely an identity-graph
        # fact, not a physics change (all three rows came from identical mean_motion/eccentricity
        # inputs above; sma_avg should be identical across the acquisition boundary).
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT sma_avg FROM v_sat_operator_daily WHERE norad_id = %s",
                (SAT_A,),
            )
            distinct_smas = cur.fetchall()
        assert len(distinct_smas) == 1, "physics aggregate must not change across the acquisition"
    finally:
        db_conn.rollback()


@pytest.mark.db
def test_v_sat_operator_daily_transition_day_attributes_single_owner(db_conn):
    """Half-open SCD2 boundary: on the exact split date (DAY_3), where ALPHA.valid_to ==
    BETA.valid_from, the satellite/day must be attributed to EXACTLY ONE operator -- the incoming
    BETA -- not double-counted under both. Guards the closed-BETWEEN regression."""
    try:
        with db_conn.cursor() as cur:
            _seed_acquisition_fixture(cur)

            cur.execute(
                "SELECT operator_name FROM v_sat_operator_daily "
                "WHERE norad_id = %s AND day::date = %s",
                (SAT_A, DAY_3),
            )
            rows = cur.fetchall()

        assert len(rows) == 1, f"transition day must yield exactly one owner row, got {rows}"
        assert rows[0][0] == OP_BETA, "boundary day belongs to the incoming operator"
    finally:
        db_conn.rollback()


@pytest.mark.db
def test_v_congestion_exposure_returns_bins(db_conn):
    try:
        with db_conn.cursor() as cur:
            _seed_acquisition_fixture(cur)

            cur.execute(
                "SELECT altitude_bin_50km, inclination_bin_5deg, bin_object_count, "
                "       operator_name, operator_object_count_in_bin, operator_exposure_contribution "
                "FROM v_congestion_exposure WHERE operator_name IN (%s, %s) "
                "ORDER BY altitude_bin_50km, inclination_bin_5deg",
                (OP_ALPHA, OP_BETA),
            )
            rows = cur.fetchall()

        assert len(rows) >= 2, "expected at least one congestion bin per synthetic operator"
        for alt_bin, inc_bin, bin_count, operator_name, op_count_in_bin, exposure in rows:
            assert isinstance(alt_bin, int)
            assert isinstance(inc_bin, int)
            assert bin_count >= op_count_in_bin > 0
            assert operator_name in (OP_ALPHA, OP_BETA)
            assert 0 < float(exposure) <= 1
    finally:
        db_conn.rollback()


@pytest.mark.db
def test_v_deorbit_compliance_flags_late_deorbit(db_conn):
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO satellite "
                "(norad_id, cospar_id, canonical_name, object_type, launch_date, decay_date) "
                "VALUES (%s, '2015-003A', 'ZZ TEST SAT DECAYED', 'PAYLOAD', '2015-01-01', "
                "'2021-01-01') RETURNING satellite_id",
                (SAT_A + 1,),
            )
            satellite_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO satellite_status_history "
                "(satellite_id, canonical_status, observed_at, source) VALUES "
                "(%s, 'ACTIVE', '2015-06-01T00:00:00Z', 'test'), "
                "(%s, 'DECAYED', '2021-06-01T00:00:00Z', 'test')",
                (satellite_id, satellite_id),
            )

            cur.execute(
                "SELECT elapsed_days, compliant FROM v_deorbit_compliance WHERE norad_id = %s",
                (SAT_A + 1,),
            )
            elapsed_days, compliant = cur.fetchone()

        assert elapsed_days > 365 * 5
        assert compliant is False
    finally:
        db_conn.rollback()
