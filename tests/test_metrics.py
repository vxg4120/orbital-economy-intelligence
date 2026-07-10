"""Tests for metrics/caggs.sql + metrics/benchmark_views.sql (Task 5).

All DB-backed. Synthetic rows use norad ids in the reserved test range
920000001-929999999 and are inserted inside db_conn's transaction, which every test rolls back
in a finally block -- nothing is ever committed to the shared dev DB.
"""

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APPLY_SCRIPT = REPO_ROOT / "scripts" / "apply_metrics.py"

# Reserved synthetic norad-id range for this task's db tests.
SAT_A = 920100001
SAT_B = 920100002

OP_ALPHA = "ZZ TEST OPERATOR ALPHA (test_metrics)"
OP_BETA = "ZZ TEST OPERATOR BETA (test_metrics)"
OP_TTO = "ZZ TEST OPERATOR TTO (test_metrics)"
OP_SK = "ZZ TEST OPERATOR SK (test_metrics)"

# Phase 2 view fixtures use their own reserved norads (distinct from SAT_A/SAT_B above).
SAT_KILLER = 920100010
SAT_TTO = 920100011
SAT_SK = 920100012

# Dynamic FUTURE dates: sat_daily is a real-time continuous aggregate, so uncommitted
# synthetic gp_elements rows are only visible in queries ABOVE the materialization
# watermark (which trails now() once any full refresh has run). Fixed past dates fall
# below the watermark and silently vanish from the view under rollback isolation.
_BASE = date.today() + timedelta(days=30)
DAY_1 = _BASE.isoformat()
DAY_2 = (_BASE + timedelta(days=1)).isoformat()
DAY_3 = (_BASE + timedelta(days=2)).isoformat()  # acquisition takes effect this day

# A run of consecutive FUTURE days for the time-to-operational orbit-raising fixture (same
# above-watermark reasoning as DAY_1..DAY_3): 3 raising days out of band, then 8 stable days in band.
TTO_DAYS = [(_BASE + timedelta(days=i)).isoformat() for i in range(11)]


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


def _seed_killer_fixture(cur):
    """One satellite whose TEMPORAL owner (SCD2) and NAIVE SATCAT owner code disagree -- the exact
    OneWeb->Eutelsat mechanic in miniature. Resolved current owner = BETA; SATCAT owner code
    'ZZOWNCODE' aliases to ALPHA. So temporal attribution credits BETA, naive-SATCAT credits ALPHA.
    """
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, rows_ingested, "
        "bytes_downloaded, status) VALUES ('satcat', 'test', now(), now(), 1, 1, 'ok') "
        "RETURNING ingest_run_id"
    )
    run_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO satellite (norad_id, cospar_id, canonical_name, object_type, launch_date) "
        "VALUES (%s, '2021-010A', 'ZZ TEST KILLER SAT', 'PAYLOAD', '2021-01-01') "
        "RETURNING satellite_id",
        (SAT_KILLER,),
    )
    sat_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO operator (canonical_name, country, operator_class) "
        "VALUES (%s, 'US', 'commercial'), (%s, 'US', 'commercial') "
        "RETURNING canonical_name, operator_id",
        (OP_ALPHA, OP_BETA),
    )
    op = dict(cur.fetchall())
    cur.execute(
        "INSERT INTO satellite_operator "
        "(satellite_id, operator_id, role, valid_from, valid_to, source) "
        "VALUES (%s, %s, 'owner', %s, NULL, 'test')",
        (sat_id, op[OP_BETA], DAY_1),
    )
    cur.execute(
        "INSERT INTO operator_alias (operator_id, alias, source) VALUES (%s, 'ZZOWNCODE', 'satcat')",
        (op[OP_ALPHA],),
    )
    cur.execute(
        "INSERT INTO source_assertion "
        "(satellite_id, source_key, attribute, value, source, observed_at, ingest_run_id) "
        "VALUES (%s, %s, 'owner', 'ZZOWNCODE', 'satcat', now(), %s)",
        (sat_id, str(SAT_KILLER), run_id),
    )
    cur.execute(
        "INSERT INTO gp_elements (norad_id, epoch, mean_motion, eccentricity, inclination, source) "
        "VALUES (%s, %s, 15.5, 0.0004, 53.0, 'test'), (%s, %s, 15.5, 0.0004, 53.0, 'test')",
        (SAT_KILLER, f"{DAY_1}T00:00:00Z", SAT_KILLER, f"{DAY_2}T00:00:00Z"),
    )


@pytest.mark.db
def test_v_killer_chart_temporal_and_naive_satcat_diverge(db_conn):
    """SPEC §12 acceptance: the same satellite is attributed to DIFFERENT operators by temporal
    identity resolution vs naive SATCAT owner codes -- the delta the killer chart visualizes."""
    try:
        with db_conn.cursor() as cur:
            _seed_killer_fixture(cur)
            cur.execute(
                "SELECT COALESCE(max(temporal_sats), 0), COALESCE(max(naive_satcat_sats), 0) "
                "FROM v_killer_chart WHERE operator_name = %s",
                (OP_BETA,),
            )
            beta_temporal, beta_naive = cur.fetchone()
            cur.execute(
                "SELECT COALESCE(max(temporal_sats), 0), COALESCE(max(naive_satcat_sats), 0) "
                "FROM v_killer_chart WHERE operator_name = %s",
                (OP_ALPHA,),
            )
            alpha_temporal, alpha_naive = cur.fetchone()
        # Resolved (SCD2) owner BETA is credited only by temporal attribution.
        assert beta_temporal >= 1 and beta_naive == 0
        # SATCAT owner code maps to ALPHA, credited only by the naive method.
        assert alpha_naive >= 1 and alpha_temporal == 0
    finally:
        db_conn.rollback()


@pytest.mark.db
def test_v_time_to_operational_measures_orbit_raising(db_conn):
    """A LEO payload spends 3 days in a lower insertion orbit (>15 km below its shell median, out of
    band) then settles into an 8-day in-band streak; time-to-operational is the first day of that
    streak (launch + 3), not launch day and not the streak's 7th day."""
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO satellite (norad_id, cospar_id, canonical_name, object_type, "
                "launch_date) VALUES (%s, '2026-011A', 'ZZ TEST TTO SAT', 'PAYLOAD', %s) "
                "RETURNING satellite_id",
                (SAT_TTO, TTO_DAYS[0]),
            )
            sat_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO operator (canonical_name, country, operator_class) "
                "VALUES (%s, 'US', 'commercial') RETURNING operator_id",
                (OP_TTO,),
            )
            op_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO satellite_operator "
                "(satellite_id, operator_id, role, valid_from, valid_to, source) "
                "VALUES (%s, %s, 'owner', %s, NULL, 'test')",
                (sat_id, op_id, TTO_DAYS[0]),
            )
            values, params = [], []
            for i, day in enumerate(TTO_DAYS):
                mean_motion = 15.7 if i < 3 else 15.5  # raising (sma ~6737) vs stable (sma ~6795)
                values.append("(%s, %s, %s, 0.0004, 53.0, 'test')")
                params += [SAT_TTO, f"{day}T00:00:00Z", mean_motion]
            cur.execute(
                "INSERT INTO gp_elements (norad_id, epoch, mean_motion, eccentricity, inclination, "
                "source) VALUES " + ", ".join(values),
                params,
            )
            cur.execute(
                "SELECT days_to_operational, operational_date FROM v_time_to_operational "
                "WHERE norad_id = %s",
                (SAT_TTO,),
            )
            row = cur.fetchone()
        assert row is not None, "a converging satellite must get a time-to-operational row"
        days, operational_date = row
        assert days == 3, f"operational on the first of 8 in-band days (launch+3), got {days}"
        assert operational_date.isoformat() == TTO_DAYS[3]
    finally:
        db_conn.rollback()


@pytest.mark.db
def test_v_station_keeping_operator_reports_p50(db_conn):
    """The per-operator station-keeping rollup returns a non-null p50 rolling-stddev for an ACTIVE
    payload with enough days to form a rolling window."""
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO satellite (norad_id, cospar_id, canonical_name, object_type, "
                "launch_date) VALUES (%s, '2020-012A', 'ZZ TEST SK SAT', 'PAYLOAD', '2020-01-01') "
                "RETURNING satellite_id",
                (SAT_SK,),
            )
            sat_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO operator (canonical_name, country, operator_class) "
                "VALUES (%s, 'US', 'commercial') RETURNING operator_id",
                (OP_SK,),
            )
            op_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO satellite_operator "
                "(satellite_id, operator_id, role, valid_from, valid_to, source) "
                "VALUES (%s, %s, 'owner', %s, NULL, 'test')",
                (sat_id, op_id, DAY_1),
            )
            cur.execute(
                "INSERT INTO satellite_status_history "
                "(satellite_id, canonical_status, observed_at, source) "
                "VALUES (%s, 'ACTIVE', now(), 'test')",
                (sat_id,),
            )
            # Three days with slightly varying sma so the rolling stddev is defined and non-zero.
            cur.execute(
                "INSERT INTO gp_elements (norad_id, epoch, mean_motion, eccentricity, inclination, "
                "source) VALUES (%s, %s, 15.50, 0.0004, 53.0, 'test'), "
                "(%s, %s, 15.51, 0.0004, 53.0, 'test'), (%s, %s, 15.52, 0.0004, 53.0, 'test')",
                (
                    SAT_SK, f"{DAY_1}T00:00:00Z",
                    SAT_SK, f"{DAY_2}T00:00:00Z",
                    SAT_SK, f"{DAY_3}T00:00:00Z",
                ),
            )
            cur.execute(
                "SELECT active_satellite_count, p50_station_keeping_km "
                "FROM v_station_keeping_operator WHERE operator_name = %s",
                (OP_SK,),
            )
            row = cur.fetchone()
        assert row is not None, "the ACTIVE seeded operator must appear in the rollup"
        active_count, p50 = row
        assert active_count >= 1
        assert p50 is not None and float(p50) >= 0.0
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
