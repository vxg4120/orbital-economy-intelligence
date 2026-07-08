"""Ledger core tests: polite_get's fetch/skip/error contract. Network mocked with `responses`,
DB assertions marked `db`."""

import datetime as dt

import pytest
import requests
import responses

from ingest import runlog
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

URL = "https://example.test/oei-ingest-fixture"


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


@pytest.mark.db
@responses.activate
def test_polite_get_on_fresh_endpoint_fetches_and_logs_ok(clean_db):
    responses.add(responses.GET, URL, body="hello world", status=200)

    resp = runlog.polite_get(clean_db, "testsrc", "ep1", URL, dt.timedelta(hours=1))

    assert resp is not None
    assert resp.text == "hello world"
    assert resp.oei_bytes == len(b"hello world")
    assert len(responses.calls) == 1
    assert responses.calls[0].request.headers["User-Agent"] == runlog.USER_AGENT

    # The caller (a loader) finishes the run once it knows the landed row count.
    runlog.finish_run(clean_db, resp.oei_run_id, rows=3, bytes_dl=resp.oei_bytes, status="ok")

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status, rows_ingested, bytes_downloaded, source, endpoint "
            "FROM ingest_run WHERE ingest_run_id = %s",
            (resp.oei_run_id,),
        )
        status, rows_ingested, bytes_dl, source, endpoint = cur.fetchone()
    assert status == "ok"
    assert rows_ingested == 3
    assert bytes_dl == len(b"hello world")
    assert source == "testsrc"
    assert endpoint == "ep1"


@pytest.mark.db
@responses.activate
def test_polite_get_skips_fresh_pull_with_zero_http_calls(clean_db):
    responses.add(responses.GET, URL, body="hello world", status=200)

    first = runlog.polite_get(clean_db, "testsrc", "ep2", URL, dt.timedelta(hours=1))
    runlog.finish_run(clean_db, first.oei_run_id, rows=1, bytes_dl=first.oei_bytes, status="ok")
    assert len(responses.calls) == 1

    second = runlog.polite_get(clean_db, "testsrc", "ep2", URL, dt.timedelta(hours=1))

    assert second is None
    assert len(responses.calls) == 1  # no new HTTP call made

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status FROM ingest_run WHERE source = %s AND endpoint = %s "
            "ORDER BY ingest_run_id DESC LIMIT 1",
            ("testsrc", "ep2"),
        )
        (status,) = cur.fetchone()
    assert status == "skipped_fresh"


@pytest.mark.db
@responses.activate
def test_polite_get_http_error_logs_error_and_raises(clean_db):
    responses.add(responses.GET, URL, body="server exploded", status=500)

    with pytest.raises(requests.HTTPError):
        runlog.polite_get(clean_db, "testsrc", "ep3", URL, dt.timedelta(hours=1))

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status FROM ingest_run WHERE source = %s AND endpoint = %s "
            "ORDER BY ingest_run_id DESC LIMIT 1",
            ("testsrc", "ep3"),
        )
        (status,) = cur.fetchone()
    assert status == "error"


@pytest.mark.db
def test_fresh_within_false_when_no_prior_run(clean_db):
    assert runlog.fresh_within(clean_db, "testsrc", "never-pulled", dt.timedelta(hours=1)) is False


@pytest.mark.db
def test_fresh_within_ignores_non_ok_runs(clean_db):
    run_id = runlog.start_run(clean_db, "testsrc", "ep4")
    runlog.finish_run(clean_db, run_id, rows=0, bytes_dl=0, status="error")
    assert runlog.fresh_within(clean_db, "testsrc", "ep4", dt.timedelta(hours=1)) is False
