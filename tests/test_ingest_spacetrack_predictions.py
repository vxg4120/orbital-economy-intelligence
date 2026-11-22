"""Space-Track's weekly 60-day reentry predictions (docs/specs/spacetrack-reentry-predictions.md).

No test makes a live request: Space-Track is mocked with `responses`. The prediction rows below
are synthetic. They copy the field names and formats of the DECAY rows production already lands
(raw_spacetrack_decay payloads on 2026-11-21, for example DECAY_EPOCH "2026-11-15 0:00:00"),
with source 60day_msg, until the first live pull gives a real sample to record.

The gate and the ledger share one clock: the tests run in January 2030, and the `clock` fixture
makes runlog.start_run stamp each ledger row with the test's time instead of the database's.
Ledger rows other tests write at the real date then always fall before the 2030 publication
times they are checked against.
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


def _msg(norad, epoch, decay, precedence, name="TESTSAT 1"):
    return {"NORAD_CAT_ID": str(norad), "OBJECT_NUMBER": str(norad), "OBJECT_NAME": name,
            "INTLDES": "2025-001A", "OBJECT_ID": "2025-001A", "COUNTRY": "US", "RCS": "0",
            "RCS_SIZE": "MEDIUM", "MSG_EPOCH": epoch, "DECAY_EPOCH": decay,
            "SOURCE": "60day_msg", "MSG_TYPE": "Prediction", "PRECEDENCE": str(precedence)}


_PREDICTIONS = [
    # One object in two bulletins of the eight-day window: this week's (newer MSG_EPOCH) wins,
    # although last week's message has the lower PRECEDENCE.
    _msg(900000601, "2030-01-01 12:00:00", "2030-01-20 0:00:00", 3),
    _msg(900000601, "2029-12-25 12:00:00", "2030-01-18 0:00:00", 1),
    _msg(900000602, "2030-01-01 12:00:00", "2030-02-11", 2, name="TEST R/B"),
    # Two messages of one issue, one without a usable date: the dated one wins.
    _msg(900000603, "2030-01-01 12:00:00", "2030", 2, name="TESTSAT 3"),
    _msg(900000603, "2030-01-01 12:00:00", "2030-01-29 3:10:00", 2, name="TESTSAT 3"),
    {"NORAD_CAT_ID": "", "SOURCE": "60day_msg"},  # degraded row: skipped, not fatal
]


def _client(conn):
    return SpaceTrackClient(conn, identity="user@example.com", password="hunter2")


@pytest.fixture
def no_throttle(monkeypatch):
    monkeypatch.setattr(SpaceTrackClient, "_throttle", lambda self: None)
    monkeypatch.setattr(spacetrack_client.time, "sleep", lambda s: None)


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    db_conn.rollback()
    cleanup_since(db_conn, baseline)


class Clock:
    def __init__(self):
        self.now = at(0, 19, 10)


@pytest.fixture
def clock(monkeypatch):
    """The test's clock, which runlog.start_run also uses for started_at."""
    c = Clock()

    def start_run(conn, source, endpoint):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ingest_run (source, endpoint, started_at) "
                        "VALUES (%s, %s, %s) RETURNING ingest_run_id", (source, endpoint, c.now))
            run_id = cur.fetchone()[0]
        conn.commit()
        return run_id

    monkeypatch.setattr(runlog, "start_run", start_run)
    return c


def _attempt(conn, clock, when, status):
    """A decay_60day ledger row that started at `when` and finished as `status`."""
    clock.now = when
    run_id = runlog.start_run(conn, sp.SOURCE, sp.ENDPOINT)
    if status != "running":
        runlog.finish_run(conn, run_id, rows=1, bytes_dl=1, status=status)
    return run_id


def _mock_space_track(rows=None, status=200):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    if status == 200:
        responses.add(responses.GET, DECAY_RE, json=rows)
    else:
        responses.add(responses.GET, DECAY_RE, status=status)


def _gets():
    return sum(1 for c in responses.calls if c.request.method == "GET")


def _pull(conn, clock, when):
    clock.now = when
    return sp.run(conn, _client(conn), now=when, out=lambda s: None)


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


def test_publication_reads_any_timezone_as_the_same_instant():
    pacific = dt.timezone(dt.timedelta(hours=-8))
    assert sp.publication(at(0, 19, 10).astimezone(pacific)) == at(0, 17)


@pytest.mark.db
@pytest.mark.parametrize("now, due", [
    (at(-1, 19, 10), False),   # Tuesday
    (at(0, 16, 59), False),    # Wednesday before 17:00
    (at(0, 17), True),         # 17:00 exactly
    (at(0, 19, 10), True),     # the Wednesday evening nightly
    (at(1, 7, 10), True),      # Thursday morning, nothing tried yet
    (at(2, 7, 10), True),      # Friday morning, still inside 48 hours
    (at(2, 17), False),        # 48 hours: the week is missed
    (at(5, 7, 10), False),     # Monday
])
def test_the_pull_is_due_only_in_the_48_hours_after_wednesday_1700(clean_db, now, due):
    assert sp.due(clean_db, now) is due


@pytest.mark.db
def test_a_week_gets_at_most_two_attempts_and_stops_at_a_success(clean_db, clock):
    _attempt(clean_db, clock, at(0, 19, 10), "error")
    assert sp.due(clean_db, at(1, 7, 10)) is True           # one retry
    _attempt(clean_db, clock, at(1, 7, 10), "error")
    assert sp.due(clean_db, at(1, 19, 10)) is False         # two attempts: the week is missed
    assert sp.due(clean_db, at(7, 19, 10)) is True          # next week starts over


@pytest.mark.db
def test_a_crashed_attempt_counts_and_a_success_ends_the_week(clean_db, clock):
    _attempt(clean_db, clock, at(0, 19, 10), "running")    # the process died mid-pull
    assert sp.due(clean_db, at(1, 7, 10)) is True
    _attempt(clean_db, clock, at(1, 7, 10), "ok")
    assert sp.due(clean_db, at(1, 19, 10)) is False
    assert sp.due(clean_db, at(2, 7, 10)) is False


# --- the query and the landing ------------------------------------------------------


def test_the_query_is_one_week_of_60_day_messages():
    assert sp.QUERY == ("/basicspacedata/query/class/decay/MSG_EPOCH/%3Enow-8/source/60day_msg"
                        "/orderby/NORAD_CAT_ID asc/format/json")
    assert sp.ENDPOINT == "decay_60day"
    assert sp.ENDPOINT != "decay"  # the readers of raw_spacetrack_decay key on 'decay'


@pytest.mark.parametrize("text, date", [
    ("2026-11-15 0:00:00", dt.date(2026, 11, 15)),
    ("2026-11-15", dt.date(2026, 11, 15)),
    ("2026-11-15T12:30:00", dt.date(2026, 11, 15)),
    ("2027", None),
    ("2026 Dec", None),
    ("2026-12-17x", None),
    ("2026-02-30", None),
    ("", None),
    (None, None),
])
def test_a_predicted_date_is_never_invented(text, date):
    assert sp.predicted_date(text) == date
    row = sp.parse_row({"NORAD_CAT_ID": "1", "DECAY_EPOCH": text})
    assert row["decay_date"] == date and row["decay_epoch"] == text


@pytest.mark.db
@responses.activate
def test_predictions_land_in_their_own_table_and_the_view_keeps_one_per_object(
        clean_db, clock, no_throttle):
    _mock_space_track(_PREDICTIONS)
    baseline = run_id_baseline(clean_db)

    assert _pull(clean_db, clock, at(0, 19, 10)) == 5

    [get] = [c.request for c in responses.calls if c.request.method == "GET"]
    assert get.url == spacetrack_client.BASE_URL + sp.QUERY.replace(" ", "%20")
    with clean_db.cursor() as cur:
        cur.execute("SELECT endpoint, status, rows_ingested FROM ingest_run "
                    "WHERE ingest_run_id > %s", (baseline,))
        assert cur.fetchall() == [("decay_60day", "ok", 5)]
        cur.execute("SELECT count(*) FROM raw_spacetrack_decay WHERE ingest_run_id > %s",
                    (baseline,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT norad_cat_id, predicted_date, decay_epoch, object_name "
                    "FROM v_reentry_prediction_latest ORDER BY 1")
        assert cur.fetchall() == [
            (900000601, dt.date(2030, 1, 20), "2030-01-20 0:00:00", "TESTSAT 1"),
            (900000602, dt.date(2030, 2, 11), "2030-02-11", "TEST R/B"),
            (900000603, dt.date(2030, 1, 29), "2030-01-29 3:10:00", "TESTSAT 3"),
        ]
        cur.execute("SELECT decay_epoch, decay_date FROM raw_spacetrack_decay_prediction "
                    "WHERE ingest_run_id > %s AND decay_epoch = '2030'", (baseline,))
        assert cur.fetchall() == [("2030", None)]  # kept verbatim, with no invented date
    # Pulled for this week: the next nightly makes no request.
    gets = _gets()
    assert _pull(clean_db, clock, at(1, 7, 10)) == 0
    assert _gets() == gets


@pytest.mark.db
@responses.activate
def test_an_empty_answer_lands_ok_and_empties_the_current_set(clean_db, clock, no_throttle):
    _mock_space_track(_PREDICTIONS[:1])
    _pull(clean_db, clock, at(0, 19, 10))
    responses.reset()
    _mock_space_track([])
    baseline = run_id_baseline(clean_db)

    assert _pull(clean_db, clock, at(7, 19, 10)) == 0

    with clean_db.cursor() as cur:
        cur.execute("SELECT status, rows_ingested FROM ingest_run WHERE ingest_run_id > %s",
                    (baseline,))
        assert cur.fetchall() == [("ok", 0)]
        cur.execute("SELECT count(*) FROM v_reentry_prediction_latest")
        assert cur.fetchone()[0] == 0  # last week's set is not shown as current


@pytest.mark.db
@responses.activate
def test_a_failing_week_stops_after_two_attempts(clean_db, clock, no_throttle):
    _mock_space_track(status=500)
    baseline = run_id_baseline(clean_db)
    with pytest.raises(Exception):
        _pull(clean_db, clock, at(0, 19, 10))
    with pytest.raises(Exception):
        _pull(clean_db, clock, at(1, 7, 10))
    gets = _gets()
    assert _pull(clean_db, clock, at(1, 19, 10)) == 0       # no third attempt
    assert _gets() == gets
    with clean_db.cursor() as cur:
        cur.execute("SELECT endpoint, status FROM ingest_run WHERE ingest_run_id > %s "
                    "ORDER BY 1", (baseline,))
        assert cur.fetchall() == [("decay_60day", "error")] * 2


@pytest.mark.db
@responses.activate
def test_rows_and_the_ok_commit_together(clean_db, clock, no_throttle, monkeypatch):
    """A failure after the rows are written leaves no row behind and the run an error."""
    _mock_space_track(_PREDICTIONS)
    baseline = run_id_baseline(clean_db)
    finish = runlog.finish_run

    def finish_failing_on_ok(conn, run_id, **kw):
        if kw.get("status") == "ok":
            raise RuntimeError("stopped before the OK")
        return finish(conn, run_id, **kw)

    monkeypatch.setattr(runlog, "finish_run", finish_failing_on_ok)
    with pytest.raises(RuntimeError):
        _pull(clean_db, clock, at(0, 19, 10))
    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_spacetrack_decay_prediction "
                    "WHERE ingest_run_id > %s", (baseline,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT status FROM ingest_run WHERE ingest_run_id > %s", (baseline,))
        assert cur.fetchall() == [("error",)]


# --- isolation from identity ---------------------------------------------------------


@pytest.fixture
def decay_state(db_conn):
    """One transaction, rolled back, with no Space-Track decay history visible (the same
    isolation tests/test_spacetrack_decay_claims.py uses)."""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM spacetrack_decay_message")
        cur.execute("DELETE FROM claim WHERE source = %s", (claims.SPACETRACK_DECAY,))
        cur.execute("DELETE FROM claim_progress WHERE source = %s", (claims.SPACETRACK_DECAY,))
        cur.execute("DELETE FROM raw_spacetrack_decay")
    try:
        yield db_conn
    finally:
        db_conn.rollback()


def _ledger_row(cur, endpoint):
    cur.execute("INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
                "VALUES ('spacetrack', %s, now(), now(), 'ok') RETURNING ingest_run_id",
                (endpoint,))
    return cur.fetchone()[0]


@pytest.mark.db
def test_a_prediction_pull_leaves_the_decay_claims_untouched(decay_state):
    """A prediction is not a decay. With a Historical landing folded into the claims, a newer
    prediction landing for the same objects changes no row of the actual-decay view, the
    folded messages, the claims or the feed's progress, and the next fold finds nothing."""
    conn = decay_state
    norads = (980000301, 980000302)
    recent = dt.datetime.now(UTC).replace(microsecond=0) - dt.timedelta(days=3)
    with conn.cursor() as cur:
        historical = _ledger_row(cur, "decay")
        for n, day in zip(norads, ("2026-11-01", "2026-11-02"), strict=True):
            cur.execute(
                "INSERT INTO raw_spacetrack_decay (norad_cat_id, decay_epoch, decay_date, "
                "msg_type, precedence, msg_epoch, source, payload, ingest_run_id) "
                "VALUES (%s, %s, %s, 'Historical', 1, %s, 'decay_msg', '{}', %s)",
                (n, day, day, recent, historical))
    assert claims.record_spacetrack_decay(conn) != (0, 0)

    def snapshot():
        with conn.cursor() as cur:
            out = []
            for sql in (
                "SELECT * FROM v_spacetrack_decay_actual ORDER BY norad_cat_id",
                "SELECT * FROM spacetrack_decay_message ORDER BY norad_cat_id, msg_epoch",
                "SELECT * FROM claim WHERE source = 'spacetrack_decay' ORDER BY claim_id",
                "SELECT * FROM claim_progress WHERE source = 'spacetrack_decay'",
            ):
                cur.execute(sql)
                out.append(cur.fetchall())
            return out

    before = snapshot()
    assert before[0] and before[1] and before[2]  # the comparison is over real rows

    with conn.cursor() as cur:
        predictions = _ledger_row(cur, sp.ENDPOINT)
    assert predictions > historical
    rows = [_msg(n, "2026-11-20 12:00:00", "2026-12-24 0:00:00", 1) for n in norads]
    sp.spacetrack_decay.land_rows(conn, rows, predictions, table=sp.TABLE, parse=sp.parse_row,
                                  commit=False)

    assert claims.record_spacetrack_decay(conn) == (0, 0)
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
