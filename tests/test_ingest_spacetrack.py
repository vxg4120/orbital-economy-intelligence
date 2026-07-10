"""Space-Track client tests: login flow, batching, backoff, missing creds. Network mocked with
`responses`; anything that touches the ingest_run ledger is marked `db`."""

import re

import pytest
import requests
import responses

from ingest import spacetrack_client
from ingest.spacetrack_client import SpaceTrackAuthError, SpaceTrackClient
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

GP_HISTORY_RE = re.compile(
    r"^https://www\.space-track\.org/basicspacedata/query/class/gp_history/NORAD_CAT_ID/.*"
)
DECAY_RE = re.compile(
    r"^https://www\.space-track\.org/basicspacedata/query/class/decay/NORAD_CAT_ID/.*"
)


def _gp_row(norad_id: int, epoch: str = "2024-01-01T00:00:00") -> dict:
    return {
        "NORAD_CAT_ID": str(norad_id),
        "EPOCH": epoch,
        "MEAN_MOTION": "15.50000000",
        "ECCENTRICITY": "0.00040000",
        "CREATION_DATE": "2024-01-01T02:00:00",
    }


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


@pytest.fixture
def no_throttle(monkeypatch):
    """These tests aren't exercising the min-interval spacing (that's covered by its own
    test below with a fake clock) — skip the real delay so they run fast and deterministically."""
    monkeypatch.setattr(SpaceTrackClient, "_throttle", lambda self: None)


def _client(conn, **kwargs):
    kwargs.setdefault("identity", "user@example.com")
    kwargs.setdefault("password", "hunter2")
    return SpaceTrackClient(conn, **kwargs)


def test_missing_credentials_raise_a_clear_error(monkeypatch):
    monkeypatch.delenv("SPACETRACK_IDENTITY", raising=False)
    monkeypatch.delenv("SPACETRACK_PASSWORD", raising=False)
    with pytest.raises(SpaceTrackAuthError, match="SPACETRACK_IDENTITY"):
        SpaceTrackClient(None)


def test_throttle_enforces_minimum_interval_via_sleep(monkeypatch):
    # 1st _throttle(): _last_request_at is None -> no wait, consumes one monotonic() call.
    # 2nd _throttle(): only 0.5s elapsed -> must sleep for the remaining 2.5s.
    clock = iter([0.0, 0.5, 3.5])
    monkeypatch.setattr(spacetrack_client.time, "monotonic", lambda: next(clock))
    sleeps = []
    monkeypatch.setattr(spacetrack_client.time, "sleep", sleeps.append)

    client = SpaceTrackClient(None, identity="u", password="p")
    client._throttle()
    client._throttle()

    assert sleeps == [2.5]


@pytest.mark.db
@responses.activate
def test_login_happens_before_first_data_request(clean_db, no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001)], status=200)

    client = _client(clean_db)
    rows = list(client.gp_history([900000001], "2024-01-01", "2024-02-01"))

    assert len(rows) == 1
    assert len(responses.calls) == 2
    assert responses.calls[0].request.method == "POST"
    assert responses.calls[0].request.url == spacetrack_client.LOGIN_URL
    assert responses.calls[1].request.method == "GET"


@pytest.mark.db
@responses.activate
def test_gp_history_batches_150_ids_into_2_requests(clean_db, no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001)], status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000101)], status=200)

    norad_ids = list(range(900000001, 900000151))  # 150 synthetic ids
    client = _client(clean_db)
    rows = list(client.gp_history(norad_ids, "2024-01-01", "2024-02-01"))

    data_calls = [c for c in responses.calls if c.request.method == "GET"]
    assert len(data_calls) == 2
    assert len(rows) == 2
    assert data_calls[0].request.url.count(",") == 99  # first batch: 100 ids -> 99 commas
    assert data_calls[1].request.url.count(",") == 49  # second batch: 50 ids -> 49 commas


@pytest.mark.db
@responses.activate
def test_backoff_retries_on_429_then_succeeds(clean_db, monkeypatch, no_throttle):
    sleeps = []
    monkeypatch.setattr(spacetrack_client.time, "sleep", sleeps.append)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, body="rate limited", status=429)
    responses.add(responses.GET, GP_HISTORY_RE, body="rate limited", status=429)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001)], status=200)

    client = _client(clean_db)
    rows = list(client.gp_history([900000001], "2024-01-01", "2024-02-01"))

    assert len(rows) == 1
    assert sleeps == [30.0, 60.0]  # base * 2**0, base * 2**1


@pytest.mark.db
@responses.activate
def test_backoff_raises_after_max_retries_exceeded(clean_db, monkeypatch, no_throttle):
    monkeypatch.setattr(spacetrack_client.time, "sleep", lambda s: None)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    for _ in range(spacetrack_client.MAX_RETRIES + 1):
        responses.add(responses.GET, GP_HISTORY_RE, body="rate limited", status=429)

    client = _client(clean_db)
    with pytest.raises(requests.HTTPError):
        list(client.gp_history([900000001], "2024-01-01", "2024-02-01"))

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status FROM ingest_run WHERE source = 'spacetrack' "
            "ORDER BY ingest_run_id DESC LIMIT 1"
        )
        (status,) = cur.fetchone()
    assert status == "error"


_RATE_LIMIT_STUB = [
    {
        "error": "You've violated your query rate limit.  Please refer to our Acceptable Use "
        "guidelines for further information on how to avoid this message in the future."
    }
]


@pytest.mark.db
@responses.activate
def test_http200_rate_limit_stub_is_retried_then_succeeds(clean_db, monkeypatch, no_throttle):
    """Space-Track throttles with HTTP 200 + [{"error": "...rate limit..."}], an error dressed as
    success. It must be retried with the 429 backoff, not returned as a degraded stub row."""
    sleeps = []
    monkeypatch.setattr(spacetrack_client.time, "sleep", sleeps.append)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=_RATE_LIMIT_STUB, status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=_RATE_LIMIT_STUB, status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001)], status=200)

    client = _client(clean_db)
    rows = list(client.gp_history([900000001], "2024-01-01", "2024-02-01"))

    assert rows == [_gp_row(900000001)]
    assert sleeps == [30.0, 60.0]  # base * 2**0, base * 2**1


@pytest.mark.db
@responses.activate
def test_http200_rate_limit_stub_raises_and_ledgers_error(clean_db, monkeypatch, no_throttle):
    """Persistent throttle stubs raise SpaceTrackRateLimitError (never silently land 0 rows), and
    the pull is recorded status='error' in the ledger so the backfill window is not checkpointed."""
    monkeypatch.setattr(spacetrack_client.time, "sleep", lambda s: None)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    for _ in range(spacetrack_client.MAX_RETRIES + 1):
        responses.add(responses.GET, GP_HISTORY_RE, json=_RATE_LIMIT_STUB, status=200)

    client = _client(clean_db)
    with pytest.raises(spacetrack_client.SpaceTrackRateLimitError):
        list(client.gp_history([900000001], "2024-01-01", "2024-02-01"))

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status FROM ingest_run WHERE source = 'spacetrack' "
            "ORDER BY ingest_run_id DESC LIMIT 1"
        )
        (status,) = cur.fetchone()
    assert status == "error"


@pytest.mark.db
@responses.activate
def test_decay_is_thin_and_does_not_land_anything(clean_db, no_throttle):
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(
        responses.GET,
        DECAY_RE,
        json=[{"NORAD_CAT_ID": "900000001", "DECAY_EPOCH": "2024-05-01T00:00:00"}],
        status=200,
    )

    client = _client(clean_db)
    rows = client.decay([900000001])

    assert rows == [{"NORAD_CAT_ID": "900000001", "DECAY_EPOCH": "2024-05-01T00:00:00"}]
    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM gp_elements WHERE norad_id = 900000001")
        (count,) = cur.fetchone()
    assert count == 0  # decay() never writes to gp_elements — landing is deferred


@responses.activate
def test_ledger_uses_stable_class_name_and_stashes_url_in_notes(monkeypatch, no_throttle):
    """Ledger hygiene: the ingest_run `endpoint` must be the stable class name ('gp_history'),
    not the 100-NORAD query URL, and the full URL is stashed in `notes` for forensics. Mocked at
    the runlog boundary so it touches neither the network ledger nor the shared dev DB (a live
    Space-Track backfill is writing that DB concurrently)."""
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001)], status=200)

    started_endpoints: list[str] = []
    finished_notes: list[str | None] = []

    def fake_start(conn, source, endpoint):
        assert source == "spacetrack"
        started_endpoints.append(endpoint)
        return len(started_endpoints)

    def fake_finish(conn, run_id, rows, bytes_dl, status, notes=None):
        finished_notes.append(notes)

    monkeypatch.setattr(spacetrack_client.runlog, "start_run", fake_start)
    monkeypatch.setattr(spacetrack_client.runlog, "finish_run", fake_finish)

    client = _client(None)
    rows = list(client.gp_history([900000001, 900000002], "2024-01-01", "2024-02-01"))

    assert rows  # rows still flow through
    # endpoint is the stable class label, never the comma-joined NORAD query URL.
    assert started_endpoints == ["gp_history"]
    # the full query URL is preserved in notes for forensics.
    assert finished_notes and finished_notes[-1] is not None
    assert "NORAD_CAT_ID" in finished_notes[-1] and "gp_history" in finished_notes[-1]


@pytest.mark.db
def test_land_gp_history_lands_into_gp_elements_with_spacetrack_source(clean_db):
    rows = [_gp_row(900000001), _gp_row(900000002)]

    n = spacetrack_client.land_gp_history(clean_db, rows)

    assert n == 2
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT norad_id, source FROM gp_elements "
            "WHERE norad_id IN (900000001, 900000002) ORDER BY norad_id"
        )
        landed = cur.fetchall()
    assert landed == [(900000001, "spacetrack_gp_history"), (900000002, "spacetrack_gp_history")]

    # Re-landing the same rows must not duplicate (ON CONFLICT DO NOTHING).
    spacetrack_client.land_gp_history(clean_db, rows)
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM gp_elements WHERE norad_id IN (900000001, 900000002)"
        )
        (count,) = cur.fetchone()
    assert count == 2


@pytest.mark.db
@responses.activate
def test_read_timeout_is_retried_with_backoff_then_succeeds(clean_db, monkeypatch, no_throttle):
    """Observed live: Space-Track gp_history can exceed the read timeout on a slow query.

    Timeouts must join the 429/5xx retry set instead of killing a multi-hour backfill.
    """
    sleeps = []
    monkeypatch.setattr(spacetrack_client.time, "sleep", sleeps.append)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, body=requests.exceptions.ReadTimeout("read timed out"))
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001)], status=200)

    client = _client(clean_db)
    rows = list(client.gp_history([900000001], "2024-01-01", "2024-02-01"))

    assert len(rows) == 1
    assert sleeps == [30.0]


@pytest.mark.db
@responses.activate
def test_read_timeout_raises_after_max_retries(clean_db, monkeypatch, no_throttle):
    monkeypatch.setattr(spacetrack_client.time, "sleep", lambda s: None)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    for _ in range(spacetrack_client.MAX_RETRIES + 1):
        responses.add(responses.GET, GP_HISTORY_RE, body=requests.exceptions.ReadTimeout("read timed out"))

    client = _client(clean_db)
    with pytest.raises(requests.exceptions.ReadTimeout):
        list(client.gp_history([900000001], "2024-01-01", "2024-02-01"))


@pytest.mark.db
@responses.activate
def test_pre_request_hook_fires_once_per_attempt_including_retries(clean_db, monkeypatch, no_throttle):
    """External pacers hook pre_request; retries are real requests and must consume budget."""
    monkeypatch.setattr(spacetrack_client.time, "sleep", lambda s: None)
    responses.add(responses.POST, spacetrack_client.LOGIN_URL, body="", status=200)
    responses.add(responses.GET, GP_HISTORY_RE, body="rate limited", status=429)
    responses.add(responses.GET, GP_HISTORY_RE, body="rate limited", status=429)
    responses.add(responses.GET, GP_HISTORY_RE, json=[_gp_row(900000001)], status=200)

    calls = []
    client = _client(clean_db, pre_request=lambda: calls.append(1))
    rows = list(client.gp_history([900000001], "2024-01-01", "2024-02-01"))

    assert len(rows) == 1
    assert len(calls) == 3  # initial attempt + two retries
