"""Space-Track's weekly 60-day reentry predictions (docs/specs/spacetrack-reentry-predictions.md).

No test makes a live request: Space-Track is mocked with `responses`. The prediction rows below
are synthetic. They copy the field names and formats of the DECAY rows production already lands
(raw_spacetrack_decay payloads on 2026-11-21, for example DECAY_EPOCH "2026-11-15 0:00:00"),
with source 60day_msg, until the first live pull gives a real sample to record.

The gate tests use dates in 2030, so that ledger rows written by other tests at the real date
always fall before the publication time they are checked against.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
import responses

from identity import claims
from ingest import runlog, spacetrack_client
from ingest import spacetrack_predictions as sp
from ingest.spacetrack_client import SpaceTrackClient
from scripts import spacetrack_topup as topup
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

UTC = dt.UTC
DECAY_RE = re.compile(r"^https://www\.space-track\.org/basicspacedata/query/class/decay/.*")
WEDNESDAY = dt.datetime(2030, 1, 2, tzinfo=UTC)  # a Wednesday


def at(days: int, hour: int, minute: int = 0) -> dt.datetime:
    """`days` after the 2030-01-02 Wednesday, at hour:minute UTC."""
    return WEDNESDAY + dt.timedelta(days=days, hours=hour, minutes=minute)


_PREDICTIONS = [
    {"NORAD_CAT_ID": "900000601", "OBJECT_NUMBER": "900000601", "OBJECT_NAME": "TESTSAT 1",
     "INTLDES": "2025-001A", "OBJECT_ID": "2025-001A", "COUNTRY": "US", "RCS": "0",
     "RCS_SIZE": "MEDIUM", "MSG_EPOCH": "2029-12-31 12:00:00",
     "DECAY_EPOCH": "2030-01-20 0:00:00", "SOURCE": "60day_msg", "MSG_TYPE": "Prediction",
     "PRECEDENCE": "3"},
    # The same object again with a lower PRECEDENCE: the view keeps this one.
    {"NORAD_CAT_ID": "900000601", "OBJECT_NUMBER": "900000601", "OBJECT_NAME": "TESTSAT 1",
     "INTLDES": "2025-001A", "OBJECT_ID": "2025-001A", "COUNTRY": "US", "RCS": "0",
     "RCS_SIZE": "MEDIUM", "MSG_EPOCH": "2029-12-30 12:00:00",
     "DECAY_EPOCH": "2030-01-18 0:00:00", "SOURCE": "60day_msg", "MSG_TYPE": "Prediction",
     "PRECEDENCE": "1"},
    {"NORAD_CAT_ID": "900000602", "OBJECT_NUMBER": "900000602", "OBJECT_NAME": "TEST R/B",
     "INTLDES": "2024-100B", "OBJECT_ID": "2024-100B", "COUNTRY": "PRC", "RCS": "0",
     "RCS_SIZE": "LARGE", "MSG_EPOCH": "2029-12-31 12:00:00", "DECAY_EPOCH": "2030-02-11",
     "SOURCE": "60day_msg", "MSG_TYPE": "Prediction", "PRECEDENCE": "2"},
    {"NORAD_CAT_ID": "", "SOURCE": "60day_msg"},  # degraded row: skipped, not fatal
]


def _client(conn):
    return SpaceTrackClient(conn, identity="user@example.com", password="hunter2")


@pytest.fixture
def no_throttle(monkeypatch):
    monkeypatch.setattr(SpaceTrackClient, "_throttle", lambda self: None)


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    db_conn.rollback()
    cleanup_since(db_conn, baseline)


def _ledger_run(conn, started_at: dt.datetime, status: str) -> int:
    """A decay_60day ledger row that started at `started_at` and finished as `status`."""
    run_id = runlog.start_run(conn, sp.SOURCE, sp.ENDPOINT)
    runlog.finish_run(conn, run_id, rows=1, bytes_dl=1, status=status)
    with conn.cursor() as cur:
        cur.execute("UPDATE ingest_run SET started_at = %s WHERE ingest_run_id = %s",
                    (started_at, run_id))
    conn.commit()
    return run_id


def _mock_space_track(rows):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, DECAY_RE, json=rows)


# --- the Wednesday gate -----------------------------------------------------------


@pytest.mark.parametrize("now, anchor", [
    (at(0, 17), at(0, 17)),             # Wednesday 17:00 exactly
    (at(0, 19, 10), at(0, 17)),         # Wednesday 19:10
    (at(0, 16, 59), at(-7, 17)),        # a minute before: last week's
    (at(1, 7, 10), at(0, 17)),          # Thursday
    (at(6, 23, 59), at(0, 17)),         # Tuesday night
])
def test_publication_is_the_most_recent_wednesday_1700_utc(now, anchor):
    assert sp.publication(now) == anchor


@pytest.mark.db
@pytest.mark.parametrize("now, due", [
    (at(-1, 19, 10), False),   # Tuesday
    (at(0, 16, 59), False),    # Wednesday before 17:00
    (at(0, 19, 10), True),     # the Wednesday evening nightly
    (at(1, 7, 10), True),      # Thursday morning, nothing pulled yet
    (at(2, 7, 10), True),      # Friday morning, still inside 48 hours
    (at(2, 19, 10), False),    # Friday evening: the week is missed
    (at(5, 7, 10), False),     # Monday
])
def test_the_pull_is_due_only_in_the_48_hours_after_wednesday_1700(clean_db, now, due):
    assert sp.due(clean_db, now) is due


@pytest.mark.db
def test_thursday_retries_a_failed_wednesday_but_not_a_successful_one(clean_db):
    _ledger_run(clean_db, at(0, 19, 10), "error")
    assert sp.due(clean_db, at(1, 7, 10)) is True
    _ledger_run(clean_db, at(1, 7, 10), "ok")
    assert sp.due(clean_db, at(1, 19, 10)) is False
    # Last week's success does not count for this week.
    assert sp.due(clean_db, at(7, 19, 10)) is True


# --- the query and the landing ------------------------------------------------------


def test_the_query_is_one_week_of_60_day_messages():
    assert sp.QUERY == ("/basicspacedata/query/class/decay/MSG_EPOCH/%3Enow-8/source/60day_msg"
                        "/orderby/NORAD_CAT_ID asc/format/json")
    assert sp.ENDPOINT == "decay_60day"
    assert sp.ENDPOINT != "decay"  # the readers of raw_spacetrack_decay key on 'decay'


@pytest.mark.db
@responses.activate
def test_predictions_land_in_their_own_table_and_the_view_keeps_one_per_object(clean_db,
                                                                                no_throttle):
    _mock_space_track(_PREDICTIONS)
    baseline = run_id_baseline(clean_db)

    n = sp.run(clean_db, _client(clean_db), now=at(0, 19, 10), out=lambda s: None)

    [get] = [c.request for c in responses.calls if c.request.method == "GET"]
    assert get.url == spacetrack_client.BASE_URL + sp.QUERY.replace(" ", "%20")
    assert n == 3
    with clean_db.cursor() as cur:
        cur.execute("SELECT endpoint, status, rows_ingested FROM ingest_run "
                    "WHERE ingest_run_id > %s", (baseline,))
        assert cur.fetchall() == [("decay_60day", "ok", 3)]
        cur.execute("SELECT count(*) FROM raw_spacetrack_decay WHERE ingest_run_id > %s",
                    (baseline,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT norad_cat_id, predicted_date, precedence, object_name "
                    "FROM v_reentry_prediction_latest ORDER BY 1")
        assert cur.fetchall() == [(900000601, dt.date(2030, 1, 18), 1, "TESTSAT 1"),
                                  (900000602, dt.date(2030, 2, 11), 2, "TEST R/B")]
    # Pulled for this week: the next nightly makes no request.
    calls = len(responses.calls)
    assert sp.run(clean_db, _client(clean_db), now=at(1, 7, 10), out=lambda s: None) == 0
    assert len(responses.calls) == calls


@pytest.mark.db
@responses.activate
def test_an_empty_answer_lands_ok_and_empties_the_current_set(clean_db, no_throttle):
    _mock_space_track(_PREDICTIONS[:1])
    sp.run(clean_db, _client(clean_db), now=at(0, 19, 10), out=lambda s: None)
    responses.reset()
    _mock_space_track([])
    baseline = run_id_baseline(clean_db)

    assert sp.run(clean_db, _client(clean_db), now=at(7, 19, 10), out=lambda s: None) == 0

    with clean_db.cursor() as cur:
        cur.execute("SELECT status, rows_ingested FROM ingest_run WHERE ingest_run_id > %s",
                    (baseline,))
        assert cur.fetchall() == [("ok", 0)]
        cur.execute("SELECT count(*) FROM v_reentry_prediction_latest")
        assert cur.fetchone()[0] == 0  # last week's set is not shown as current


@pytest.mark.db
@responses.activate
def test_an_http_error_ledgers_an_error_and_leaves_the_week_due(clean_db, no_throttle,
                                                                monkeypatch):
    monkeypatch.setattr(spacetrack_client.time, "sleep", lambda s: None)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, DECAY_RE, status=500)
    baseline = run_id_baseline(clean_db)
    with pytest.raises(Exception):
        sp.run(clean_db, _client(clean_db), now=at(0, 19, 10), out=lambda s: None)
    with clean_db.cursor() as cur:
        cur.execute("SELECT endpoint, status FROM ingest_run WHERE ingest_run_id > %s",
                    (baseline,))
        assert cur.fetchall() == [("decay_60day", "error")]
    assert sp.due(clean_db, at(1, 7, 10)) is True


@pytest.mark.db
@responses.activate
def test_a_prediction_pull_leaves_the_decay_claims_untouched(clean_db, no_throttle):
    """A prediction is not a decay: the actual-decay view and the claims pass's work list are
    identical before and after a predictions run."""
    def snapshot():
        with clean_db.cursor() as cur:
            cur.execute("SELECT * FROM v_spacetrack_decay_actual ORDER BY norad_cat_id")
            view = cur.fetchall()
            cur.execute("SELECT count(*), max(last_run) FROM spacetrack_decay_message")
            messages = cur.fetchone()
            unfolded = claims._unfolded_runs(cur, 0)
        clean_db.commit()
        return view, messages, unfolded

    before = snapshot()
    _mock_space_track(_PREDICTIONS)
    assert sp.run(clean_db, _client(clean_db), now=at(0, 19, 10), out=lambda s: None) == 3
    assert snapshot() == before


# --- the nightly -------------------------------------------------------------------


def test_raw_spacetrack_decay_prediction_is_pruned_like_every_raw_table():
    from scripts.prune_snapshots import FOLDED_BY, SNAPSHOT_TABLES
    assert "raw_spacetrack_decay_prediction" in SNAPSHOT_TABLES
    assert "raw_spacetrack_decay_prediction" not in FOLDED_BY


@pytest.mark.db
def test_the_top_up_runs_the_predictions_after_the_decay_pull(monkeypatch, clean_db):
    monkeypatch.setenv("SPACETRACK_IDENTITY", "user@example.com")
    monkeypatch.setenv("SPACETRACK_PASSWORD", "hunter2")
    order = []
    monkeypatch.setattr(topup, "gp_topup", lambda *a, **k: order.append("gp"))
    monkeypatch.setattr(topup.spacetrack_decay, "run", lambda *a, **k: order.append("decay"))
    monkeypatch.setattr(topup.spacetrack_predictions, "run",
                        lambda conn, client, now=None, out=print: order.append(("pred", now)))
    now = at(0, 19, 10)
    assert topup.main([], conn=clean_db, client_factory=lambda c, **kw: object(), now=now) == 0
    assert order == ["gp", "decay", ("pred", now)]
    order.clear()
    assert topup.main(["--only", "predictions"], conn=clean_db,
                      client_factory=lambda c, **kw: object(), now=now) == 0
    assert order == [("pred", now)]
    order.clear()
    topup.main(["--dry-run"], conn=clean_db, client_factory=lambda c, **kw: object(), now=now)
    assert order == ["gp"]  # a dry run makes no predictions request
