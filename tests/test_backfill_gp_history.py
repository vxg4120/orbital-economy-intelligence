"""Tests for scripts/backfill_gp_history.py.

Pure-logic tests (plan math, pacing, checkpoint skip/resume) run with mocked clients and no
network or DB. The fleet-selection test is `db`-marked and read-only against the live catalog.
"""

import datetime as dt

import pytest

from scripts import backfill_gp_history as bf


# --- fakes --------------------------------------------------------------------


class FakeClient:
    """Records each gp_history call and yields a fixed number of OMM-shaped rows per batch."""

    def __init__(self, rows_per_batch: int = 1):
        self.calls: list[tuple] = []
        self.rows_per_batch = rows_per_batch

    def gp_history(self, norad_ids, created_since, created_before):
        self.calls.append((tuple(norad_ids), created_since, created_before))
        for _ in range(self.rows_per_batch):
            yield {"NORAD_CAT_ID": norad_ids[0], "EPOCH": "2025-01-01T00:00:00"}


def _fake_land(conn, rows):
    return len(rows)


def _no_wait_pacer():
    # max huge + static clock => acquire never sleeps.
    return bf.HourlyPacer(10_000, now=lambda: 0.0, sleep=lambda s: None)


# --- window + plan math -------------------------------------------------------


def test_monthly_windows_default_span_is_13_windows():
    windows = bf.monthly_windows(dt.date(2025, 7, 1), dt.date(2026, 7, 9))
    assert len(windows) == 13
    assert windows[0] == (dt.date(2025, 7, 1), dt.date(2025, 8, 1))
    assert windows[-1] == (dt.date(2026, 7, 1), dt.date(2026, 7, 9))  # final window clipped to until


def test_monthly_windows_single_month():
    assert bf.monthly_windows(dt.date(2026, 6, 1), dt.date(2026, 7, 1)) == [
        (dt.date(2026, 6, 1), dt.date(2026, 7, 1))
    ]


def test_monthly_windows_empty_when_since_ge_until():
    assert bf.monthly_windows(dt.date(2026, 7, 1), dt.date(2026, 7, 1)) == []


def test_plan_math_ids_times_windows():
    fleets = {"OpA": list(range(250)), "OpB": list(range(50)), "OpC": []}
    plan = bf.make_plan(lambda n: fleets[n], ["OpA", "OpB", "OpC"], n_windows=3)
    by_op = {p["operator"]: p for p in plan}
    # 250 ids -> ceil(250/100)=3 batches; x3 windows -> 9 requests
    assert by_op["OpA"]["n_batches"] == 3
    assert by_op["OpA"]["est_requests"] == 9
    # 50 ids -> 1 batch; x3 -> 3
    assert by_op["OpB"]["n_batches"] == 1
    assert by_op["OpB"]["est_requests"] == 3
    # 0 ids -> 0 batches -> 0 requests
    assert by_op["OpC"]["n_batches"] == 0
    assert by_op["OpC"]["est_requests"] == 0


def test_format_plan_reports_total(capsys):
    plan = bf.make_plan(lambda n: list(range(120)), ["OpA"], n_windows=2)
    windows = [(dt.date(2025, 7, 1), dt.date(2025, 8, 1)), (dt.date(2025, 8, 1), dt.date(2025, 9, 1))]
    text = bf.format_plan(plan, windows)
    assert "TOTAL" in text
    assert "4" in text  # ceil(120/100)=2 batches x 2 windows = 4 est requests


# --- operator resolution ------------------------------------------------------


def test_resolve_operators_maps_aliases():
    assert bf.resolve_operators(["Capella", "Kuiper", "spacex"]) == [
        "Capella Space",
        "Amazon",
        "SpaceX",
    ]


def test_resolve_operators_dedupes():
    assert bf.resolve_operators(["SpaceX", "starlink"]) == ["SpaceX"]


def test_resolve_operators_unknown_raises():
    with pytest.raises(ValueError, match="unknown operator"):
        bf.resolve_operators(["NotAnOperator"])


# --- hourly pacer -------------------------------------------------------------


def test_pacer_allows_up_to_max_without_sleeping():
    pacer = bf.HourlyPacer(3, now=lambda: 0.0, sleep=lambda s: pytest.fail("should not sleep"))
    assert [pacer.acquire() for _ in range(3)] == [0.0, 0.0, 0.0]


def test_pacer_sleeps_until_oldest_request_exits_window():
    clock = {"t": 0.0}
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        clock["t"] += s  # advance the clock as a real sleep would

    pacer = bf.HourlyPacer(3, now=lambda: clock["t"], sleep=fake_sleep)
    for _ in range(3):
        pacer.acquire()  # fills the window at t=0
    slept = pacer.acquire()  # 4th request must wait a full hour for the oldest to age out
    assert slept == 3600.0
    assert sleeps == [3600.0]


def test_pacer_partial_wait_when_some_requests_have_aged():
    clock = {"t": 0.0}
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        clock["t"] += s

    pacer = bf.HourlyPacer(2, now=lambda: clock["t"], sleep=fake_sleep)
    pacer.acquire()  # t=0
    clock["t"] = 1000.0
    pacer.acquire()  # t=1000
    slept = pacer.acquire()  # window full; oldest (t=0) exits at 3600 => wait 2600
    assert slept == 2600.0


# --- checkpoint + runner ------------------------------------------------------


def _small_plan():
    return [
        {"operator": "OpA", "ids": [1, 2, 3], "n_batches": 1, "est_requests": 2},
        {"operator": "OpB", "ids": list(range(150)), "n_batches": 2, "est_requests": 4},
    ]


_WINDOWS = [
    (dt.date(2025, 7, 1), dt.date(2025, 8, 1)),
    (dt.date(2025, 8, 1), dt.date(2025, 9, 1)),
]


def test_checkpoint_roundtrip_and_reset(tmp_path):
    path = tmp_path / "cp.json"
    cp = bf.Checkpoint(path)
    cp.mark_done("OpA|2025-07-01|2025-08-01", rows=10, requests=1)
    # A fresh load sees the persisted unit.
    cp2 = bf.Checkpoint(path)
    assert cp2.is_done("OpA|2025-07-01|2025-08-01")
    assert cp2.rows_done() == 10
    cp2.reset()
    assert not path.exists()
    assert bf.Checkpoint(path).units == {}


def test_run_backfill_skips_checkpointed_units(tmp_path):
    cp = bf.Checkpoint(tmp_path / "cp.json")
    done_key = bf.unit_key("OpA", _WINDOWS[0])
    cp.mark_done(done_key, rows=99, requests=1)  # pre-mark one unit as complete

    client = FakeClient(rows_per_batch=2)
    summary = bf.run_backfill(
        None, client, _small_plan(), _WINDOWS, cp,
        land_fn=_fake_land,
    )

    # The pre-done OpA/window0 unit must not have been requested again.
    called_windows = {(c[1], c[2]) for c in client.calls}
    assert ("2025-07-01", "2025-08-01") in called_windows  # OpB still ran for window0
    # OpA window0 had ids [1,2,3]; assert no batch for exactly those ids in that window.
    assert ((1, 2, 3), "2025-07-01", "2025-08-01") not in client.calls
    # Total requests issued this run = all units except the one skipped.
    # OpA: 1 window x 1 batch (the other window skipped-off) ; OpB: 2 windows x 2 batches = 4.
    assert summary["requests_issued"] == 1 + 4
    # Every (operator, window) unit is now checkpointed.
    assert len(cp.units) == 4


def test_run_backfill_full_resume_is_a_noop(tmp_path):
    path = tmp_path / "cp.json"
    cp = bf.Checkpoint(path)
    first = bf.run_backfill(
        None, FakeClient(), _small_plan(), _WINDOWS, cp,
        land_fn=_fake_land,
    )
    assert first["requests_issued"] == 1 * 2 + 2 * 2  # OpA 2 units x1 batch + OpB 2 units x2 batches
    assert len(cp.units) == 4

    # Reload from disk and re-run: nothing left to do, zero new requests.
    cp_reload = bf.Checkpoint(path)
    client2 = FakeClient()
    second = bf.run_backfill(
        None, client2, _small_plan(), _WINDOWS, cp_reload,
        land_fn=_fake_land,
    )
    assert client2.calls == []
    assert second["requests_issued"] == 0


def test_run_backfill_stop_mid_run_leaves_unit_uncheckpointed(tmp_path):
    cp = bf.Checkpoint(tmp_path / "cp.json")
    # Stop as soon as the first batch has landed.
    state = {"n": 0}

    def stop_after_first():
        state["n"] += 1
        return state["n"] >= 1  # request stop immediately after the first batch check

    summary = bf.run_backfill(
        None, FakeClient(), _small_plan(), _WINDOWS, cp,
        land_fn=_fake_land, stop_requested=stop_after_first,
    )
    assert summary["stopped"] is True
    # The in-progress unit must NOT be checkpointed (so it restarts idempotently).
    assert len(cp.units) == 0


def test_run_backfill_counts_rows(tmp_path):
    cp = bf.Checkpoint(tmp_path / "cp.json")
    client = FakeClient(rows_per_batch=3)
    summary = bf.run_backfill(
        None, client, _small_plan(), _WINDOWS, cp,
        land_fn=_fake_land,
    )
    # OpA: 2 windows x 1 batch x 3 rows = 6 ; OpB: 2 windows x 2 batches x 3 rows = 12 => 18
    assert summary["cumulative_rows"] == 18
    assert cp.rows_done() == 18


# --- live DB fleet selection (read-only) --------------------------------------


@pytest.mark.db
def test_verify_operators_all_benchmark_present(db_conn):
    bf.verify_operators(db_conn, bf.BENCHMARK_OPERATORS)  # raises if any missing


@pytest.mark.db
def test_fleet_selection_resolves_all_benchmark_operators(db_conn):
    for name in bf.BENCHMARK_OPERATORS:
        ids = bf.fleet_ids(db_conn, name)
        assert len(ids) > 0, f"{name} resolved to 0 fleet ids"
        assert all(isinstance(n, int) for n in ids)


@pytest.mark.db
def test_fleet_selection_spacex_exceeds_5000(db_conn):
    assert len(bf.fleet_ids(db_conn, "SpaceX")) > 5000


@pytest.mark.db
def test_eutelsat_fleet_includes_oneweb_children(db_conn):
    # Eutelsat's fleet (via the OneWeb merged_into child) is far larger than a lone GEO operator's.
    assert len(bf.fleet_ids(db_conn, "Eutelsat")) > 100
