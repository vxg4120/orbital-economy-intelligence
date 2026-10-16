"""The nightly Space-Track top-up (scripts/spacetrack_topup.py, ingest/spacetrack_decay.py).

Network is faked with `responses`, as in tests/test_ingest_spacetrack.py; anything touching the
ledger, gp_elements, the cursor or raw_spacetrack_decay is marked `db` and cleans up after
itself (synthetic NORAD ids from the reserved 900000001+ range)."""

from __future__ import annotations

import collections
import datetime as dt
import re

import pytest
import requests
import responses

from ingest import spacetrack_client, spacetrack_decay
from ingest.spacetrack_client import SpaceTrackClient
from scripts import backfill_gp_history as bf
from scripts import spacetrack_topup as topup
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

GP_HISTORY_RE = re.compile(
    r"^https://www\.space-track\.org/basicspacedata/query/class/gp_history/NORAD_CAT_ID/.*"
)
DECAY_RE = re.compile(r"^https://www\.space-track\.org/basicspacedata/query/class/decay/.*")
NOW = dt.datetime(2026, 10, 16, 7, 20, 0, tzinfo=dt.timezone.utc)


def _gp_row(norad_id: int, epoch: str) -> dict:
    return {"NORAD_CAT_ID": str(norad_id), "EPOCH": epoch, "MEAN_MOTION": "15.5",
            "ECCENTRICITY": "0.0004", "CREATION_DATE": "2026-10-15T02:00:00"}


def _client(conn, **kw):
    return SpaceTrackClient(conn, identity="user@example.com", password="hunter2", **kw)


@pytest.fixture
def no_throttle(monkeypatch):
    monkeypatch.setattr(SpaceTrackClient, "_throttle", lambda self: None)


@pytest.fixture
def clean_db(db_conn):
    """Baseline the ledger, and stash then restore the top-up cursor (the stream is fixed)."""
    baseline = run_id_baseline(db_conn)
    cursor_before = topup.read_cursor(db_conn)
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM spacetrack_cursor WHERE stream = %s", (topup.CURSOR_STREAM,))
    db_conn.commit()
    yield db_conn
    db_conn.rollback()
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM spacetrack_cursor WHERE stream = %s", (topup.CURSOR_STREAM,))
        if cursor_before is not None:
            cur.execute("INSERT INTO spacetrack_cursor (stream, through) VALUES (%s, %s)",
                        (topup.CURSOR_STREAM, cursor_before))
    db_conn.commit()
    cleanup_since(db_conn, baseline)


def _fleet(ids_by_op):
    return lambda name: ids_by_op[name]


def _gp_count(conn, low=900000001, high=900000999):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM gp_elements WHERE norad_id BETWEEN %s AND %s",
                    (low, high))
        return cur.fetchone()[0]


def _ledger(conn, baseline, endpoint):
    with conn.cursor() as cur:
        cur.execute("SELECT status, rows_ingested, notes FROM ingest_run WHERE source = "
                    "'spacetrack' AND endpoint = %s AND ingest_run_id > %s "
                    "ORDER BY ingest_run_id", (endpoint, baseline))
        return cur.fetchall()


# --- no credentials -----------------------------------------------------------


@pytest.mark.parametrize("identity,password", [("", ""), ("u@example.com", ""), ("", "pw"),
                                               ("  ", "  ")])
@responses.activate
def test_without_credentials_it_logs_one_line_and_exits_0_without_any_request(
        monkeypatch, identity, password):
    monkeypatch.setenv("SPACETRACK_IDENTITY", identity)
    monkeypatch.setenv("SPACETRACK_PASSWORD", password)
    monkeypatch.setattr(topup, "get_conn", lambda: pytest.fail("must not connect"))
    lines = []
    assert topup.main([], out=lines.append) == 0
    assert len(lines) == 1 and "not set" in lines[0]
    assert len(responses.calls) == 0


def test_unset_credentials_also_no_op(monkeypatch):
    monkeypatch.delenv("SPACETRACK_IDENTITY", raising=False)
    monkeypatch.delenv("SPACETRACK_PASSWORD", raising=False)
    monkeypatch.setattr(topup, "get_conn", lambda: pytest.fail("must not connect"))
    lines = []
    assert topup.main([], out=lines.append) == 0
    assert len(lines) == 1


def test_the_nightly_passes_the_credentials_and_runs_between_ingest_and_graph():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "deploy" / "nightly-refresh.sh").read_text()
    line = next(ln for ln in text.splitlines() if "scripts/spacetrack_topup.py" in ln
                or "step oei_spacetrack" in ln)
    assert "step oei_spacetrack" in line
    assert text.index("step oei_ingest_all") < text.index("step oei_spacetrack") \
        < text.index("step oei_build_graph")
    block = text[text.index("step oei_spacetrack"):text.index("step oei_build_graph")]
    assert 'SPACETRACK_IDENTITY="${SPACETRACK_IDENTITY:-}"' in block
    assert 'SPACETRACK_PASSWORD="${SPACETRACK_PASSWORD:-}"' in block
    assert "|| echo \"!!" in block


# --- window -------------------------------------------------------------------


@pytest.mark.db
def test_first_window_starts_three_days_back_then_at_the_cursor(clean_db):
    since, through = topup.topup_window(clean_db, NOW)
    assert since == dt.datetime(2026, 10, 13, 7, 20, 0)
    assert through == dt.datetime(2026, 10, 16, 5, 20, 0)   # now minus the 2 h margin
    topup._write_cursor(clean_db, dt.datetime(2026, 10, 15, 7, 31, 5, tzinfo=dt.timezone.utc))
    since, _ = topup.topup_window(clean_db, NOW)
    assert since == dt.datetime(2026, 10, 15, 7, 31, 5)


@pytest.mark.db
def test_a_gap_beyond_30_days_is_refused_with_the_backfill_command(clean_db):
    topup._write_cursor(clean_db, NOW - dt.timedelta(days=31))
    with pytest.raises(topup.TopupGapError, match="backfill_gp_history.py --since 2026-09-15"):
        topup.topup_window(clean_db, NOW)


# --- gp_history top-up --------------------------------------------------------


@pytest.mark.db
@responses.activate
def test_topup_batches_the_fleet_and_queries_only_the_creation_window(clean_db, no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE,
                  json=[_gp_row(900000001, "2026-10-14T00:00:00"),
                        _gp_row(900000001, "2026-10-15T00:00:00")])
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000101, "2026-10-14T00:00:00")])
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000201, "2026-10-14T00:00:00")])
    baseline = run_id_baseline(clean_db)
    fleet = _fleet({"OpA": list(range(900000001, 900000151)), "OpB": [900000201]})

    n = topup.gp_topup(clean_db, _client(clean_db), now=NOW, fleet_provider=fleet,
                       operators=["OpA", "OpB"], out=lambda s: None)

    gets = [c.request.url for c in responses.calls if c.request.method == "GET"]
    assert len(gets) == 3                                   # 150 ids -> 2 batches, 1 id -> 1
    assert [u.split("/NORAD_CAT_ID/")[1].split("/")[0].count(",") for u in gets] == [99, 49, 0]
    for url in gets:
        assert "/CREATION_DATE/2026-10-13T07:20:00--2026-10-16T05:20:00/" in url
    assert n == 4 and _gp_count(clean_db) == 4
    assert topup.read_cursor(clean_db) == NOW - topup.PUBLICATION_MARGIN
    (status, rows, notes), = _ledger(clean_db, baseline, topup.TOPUP_ENDPOINT)
    assert (status, rows) == ("ok", 4) and "3 request(s)" in notes


@pytest.mark.db
@responses.activate
def test_running_twice_adds_no_rows_and_the_second_window_starts_where_the_first_ended(
        clean_db, no_throttle, monkeypatch):
    monkeypatch.setattr(topup, "TOPUP_MIN_INTERVAL", dt.timedelta(0))  # bypass the daily gate
    rows = [_gp_row(900000001, "2026-10-14T00:00:00"), _gp_row(900000002, "2026-10-14T06:00:00")]
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=rows)
    responses.add(responses.GET, GP_HISTORY_RE, json=rows)  # Space-Track repeats itself
    fleet = _fleet({"OpA": [900000001, 900000002]})
    client = _client(clean_db)

    topup.gp_topup(clean_db, client, now=NOW, fleet_provider=fleet, operators=["OpA"],
                   out=lambda s: None)
    assert _gp_count(clean_db) == 2
    later = NOW + dt.timedelta(days=1)
    topup.gp_topup(clean_db, client, now=later, fleet_provider=fleet, operators=["OpA"],
                   out=lambda s: None)

    assert _gp_count(clean_db) == 2
    second = [c.request.url for c in responses.calls if c.request.method == "GET"][1]
    assert "/CREATION_DATE/2026-10-16T05:20:00--2026-10-17T05:20:00/" in second
    assert topup.read_cursor(clean_db) == later - topup.PUBLICATION_MARGIN


@pytest.mark.db
@responses.activate
def test_a_second_run_within_the_day_is_skipped_without_a_request(clean_db, no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001, "2026-10-14T00:00:00")])
    baseline = run_id_baseline(clean_db)
    fleet = _fleet({"OpA": [900000001]})
    client = _client(clean_db)
    topup.gp_topup(clean_db, client, now=NOW, fleet_provider=fleet, operators=["OpA"],
                   out=lambda s: None)
    calls = len(responses.calls)

    assert topup.gp_topup(clean_db, client, now=NOW + dt.timedelta(hours=12),
                          fleet_provider=fleet, operators=["OpA"], out=lambda s: None) == 0
    assert len(responses.calls) == calls
    assert [r[0] for r in _ledger(clean_db, baseline, topup.TOPUP_ENDPOINT)] == \
        ["ok", "skipped_fresh"]
    assert topup.read_cursor(clean_db) == NOW - topup.PUBLICATION_MARGIN


@pytest.mark.db
@responses.activate
def test_a_failed_batch_leaves_the_cursor_put_and_ledgers_an_error(clean_db, no_throttle,
                                                                   monkeypatch):
    monkeypatch.setattr(spacetrack_client.time, "sleep", lambda s: None)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001, "2026-10-14T00:00:00")])
    for _ in range(spacetrack_client.MAX_RETRIES + 1):
        responses.add(responses.GET, GP_HISTORY_RE, body="down", status=503)
    baseline = run_id_baseline(clean_db)
    fleet = _fleet({"OpA": list(range(900000001, 900000201))})  # 2 batches; the 2nd fails

    with pytest.raises(requests.HTTPError):
        topup.gp_topup(clean_db, _client(clean_db), now=NOW, fleet_provider=fleet,
                       operators=["OpA"], out=lambda s: None)

    assert topup.read_cursor(clean_db) is None
    (status, _, notes), = _ledger(clean_db, baseline, topup.TOPUP_ENDPOINT)
    assert status == "error" and "CREATION_DATE 2026-10-13T07:20:00" in notes


@pytest.mark.db
def test_an_empty_fleet_is_an_error_and_leaves_the_cursor_put(clean_db):
    baseline = run_id_baseline(clean_db)
    with pytest.raises(topup.TopupEmptyError, match="no NORAD ids"):
        topup.gp_topup(clean_db, _client(clean_db), now=NOW, fleet_provider=_fleet({"OpA": []}),
                       operators=["OpA"], out=lambda s: None)
    assert topup.read_cursor(clean_db) is None
    (status, _, notes), = _ledger(clean_db, baseline, topup.TOPUP_ENDPOINT)
    assert status == "error" and "no NORAD ids" in notes


@pytest.mark.db
@responses.activate
def test_a_large_fleet_returning_nothing_is_an_error_and_leaves_the_cursor_put(
        clean_db, no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[])
    responses.add(responses.GET, GP_HISTORY_RE, json=[])
    baseline = run_id_baseline(clean_db)
    fleet = _fleet({"OpA": list(range(900000001, 900000001 + topup.EMPTY_WINDOW_MIN_IDS + 50))})
    with pytest.raises(topup.TopupEmptyError, match="returned no element set"):
        topup.gp_topup(clean_db, _client(clean_db), now=NOW, fleet_provider=fleet,
                       operators=["OpA"], out=lambda s: None)
    assert topup.read_cursor(clean_db) is None
    (status, _, notes), = _ledger(clean_db, baseline, topup.TOPUP_ENDPOINT)
    assert status == "error" and "150 ids in 2 request(s)" in notes


@pytest.mark.db
@responses.activate
def test_a_small_fleet_returning_nothing_is_a_quiet_night(clean_db, no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[])
    topup.gp_topup(clean_db, _client(clean_db), now=NOW, fleet_provider=_fleet({"OpA": [900000001]}),
                   operators=["OpA"], out=lambda s: None)
    assert topup.read_cursor(clean_db) == NOW - topup.PUBLICATION_MARGIN


@pytest.mark.db
def test_a_plan_over_the_nightly_budget_is_refused_before_any_request(clean_db):
    class NoCalls:
        def gp_history(self, *a):
            pytest.fail("no request may be made")

    fleet = _fleet({"OpA": list(range(topup.MAX_PLANNED_REQUESTS * 100 + 1))})
    with pytest.raises(topup.TopupBudgetError):
        topup.gp_topup(clean_db, NoCalls(), now=NOW, fleet_provider=fleet, operators=["OpA"],
                       out=lambda s: None)


# --- decay ----------------------------------------------------------------------

_DECAYS = [
    {"NORAD_CAT_ID": "900000501", "OBJECT_NAME": "STARLINK-9001", "INTLDES": "2020-001A",
     "COUNTRY": "US", "MSG_EPOCH": "2026-10-10 04:00:00", "DECAY_EPOCH": "2026-10-09",
     "SOURCE": "decay_msg", "MSG_TYPE": "Historical", "PRECEDENCE": "2"},
    {"NORAD_CAT_ID": "900000501", "OBJECT_NAME": "STARLINK-9001", "INTLDES": "2020-001A",
     "COUNTRY": "US", "MSG_EPOCH": "2026-10-11 04:00:00", "DECAY_EPOCH": "2026-10-08 23:10:00",
     "SOURCE": "satcat", "MSG_TYPE": "Historical", "PRECEDENCE": "1"},
    {"NORAD_CAT_ID": "900000502", "OBJECT_NAME": "DEB", "INTLDES": "1999-025XY",
     "COUNTRY": "PRC", "MSG_EPOCH": "2026-10-01 00:00:00", "DECAY_EPOCH": "2026-09-30",
     "SOURCE": "decay_msg", "MSG_TYPE": "Historical", "PRECEDENCE": "1"},
    {"NORAD_CAT_ID": "", "MSG_TYPE": "Historical"},  # degraded row: skipped, not fatal
]


@pytest.mark.db
@responses.activate
def test_decay_messages_land_and_the_view_keeps_the_most_recent_per_object(clean_db,
                                                                            no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, DECAY_RE, json=_DECAYS)
    baseline = run_id_baseline(clean_db)

    n = spacetrack_decay.run(clean_db, _client(clean_db), out=lambda s: None)

    url = [c.request.url for c in responses.calls if c.request.method == "GET"][0]
    assert "/class/decay/MSG_TYPE/Historical/MSG_EPOCH/%3Enow-30/" in url
    assert n == 3
    with clean_db.cursor() as cur:
        cur.execute("SELECT norad_cat_id, decay_date, source FROM v_spacetrack_decay_actual "
                    "WHERE norad_cat_id BETWEEN 900000501 AND 900000502 ORDER BY 1")
        assert cur.fetchall() == [(900000501, dt.date(2026, 10, 8), "satcat"),
                                  (900000502, dt.date(2026, 9, 30), "decay_msg")]
        cur.execute("SELECT payload->>'OBJECT_NAME', msg_epoch FROM raw_spacetrack_decay "
                    "WHERE norad_cat_id = 900000502 AND ingest_run_id > %s", (baseline,))
        assert cur.fetchone() == ("DEB", dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc))
    (status, rows, _), = _ledger(clean_db, baseline, spacetrack_decay.ENDPOINT)
    assert (status, rows) == ("ok", 3)


@pytest.mark.db
@responses.activate
def test_decay_is_pulled_at_most_once_a_day(clean_db, no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, DECAY_RE, json=_DECAYS[:1])
    client = _client(clean_db)
    spacetrack_decay.run(clean_db, client, out=lambda s: None)
    calls = len(responses.calls)
    assert spacetrack_decay.run(clean_db, client, out=lambda s: None) == 0
    assert len(responses.calls) == calls
    assert spacetrack_decay.MIN_INTERVAL >= dt.timedelta(hours=23)


def test_raw_spacetrack_decay_is_pruned_like_every_raw_table():
    from scripts.prune_snapshots import SNAPSHOT_TABLES
    assert "raw_spacetrack_decay" in SNAPSHOT_TABLES


# --- rate limits ------------------------------------------------------------------


@responses.activate
def test_requests_stay_under_30_a_minute_and_300_an_hour(monkeypatch):
    """Drive 400 queries through the real client throttle and the script's pacer on a fake
    clock that only moves when something sleeps; count request starts in sliding windows."""
    clock = {"t": 0.0}

    def sleep(s):
        clock["t"] += s

    monkeypatch.setattr(spacetrack_client.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(spacetrack_client.time, "sleep", sleep)
    monkeypatch.setattr(spacetrack_client.runlog, "start_run", lambda *a: 1)
    monkeypatch.setattr(spacetrack_client.runlog, "finish_run", lambda *a, **k: None)
    starts = []
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add_callback(responses.GET, GP_HISTORY_RE,
                           callback=lambda req: (starts.append(clock["t"]) or (200, {}, "[]")))
    pacer = bf.HourlyPacer(topup.MAX_REQUESTS_PER_HOUR, now=lambda: clock["t"], sleep=sleep)
    client = _client(None, pre_request=pacer.acquire)

    for i in range(400):
        list(client.gp_history([900000001 + i], "2026-10-13T00:00:00", "2026-10-16T00:00:00"))

    assert len(starts) == 400
    def max_in(window):
        q, worst = collections.deque(), 0
        for t in starts:
            q.append(t)
            while q[0] <= t - window:
                q.popleft()
            worst = max(worst, len(q))
        return worst
    assert max_in(60.0) <= 20 < 30
    assert max_in(3600.0) <= topup.MAX_REQUESTS_PER_HOUR < 300
