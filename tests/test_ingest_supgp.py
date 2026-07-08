"""SupGP cross-tag anomaly scraper tests: defensive HTML parsing against a saved fixture."""

from pathlib import Path

import pytest
import responses

from ingest import supgp_crosstags
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

FIXTURE = Path(__file__).parent / "fixtures" / "supgp_index_sample.html"
FIXTURE_NO_ANOMALIES = Path(__file__).parent / "fixtures" / "supgp_index_no_anomalies.html"


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


def test_extract_anomaly_rows_finds_no_match_and_cross_tag_flags():
    anomalies = supgp_crosstags.extract_anomaly_rows(FIXTURE.read_text())

    assert len(anomalies) == 2
    flags = {a["norad_id"]: a["flag"] for a in anomalies}
    assert flags[900000002] == "NO MATCH"
    assert flags[900000003] == "CROSS-TAG"
    ok_row_norad_ids = {a["norad_id"] for a in anomalies}
    assert 900000001 not in ok_row_norad_ids  # "OK" rows are not anomalies


def test_extract_anomaly_rows_returns_empty_list_when_nothing_matches():
    assert supgp_crosstags.extract_anomaly_rows(FIXTURE_NO_ANOMALIES.read_text()) == []


@pytest.mark.db
@responses.activate
def test_run_lands_anomaly_rows(clean_db):
    responses.add(responses.GET, supgp_crosstags.URL, body=FIXTURE.read_text(), status=200)

    n = supgp_crosstags.run(clean_db)

    assert n == 2
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT norad_id, flag FROM raw_supgp_status "
            "WHERE norad_id BETWEEN 900000001 AND 900000004 ORDER BY norad_id"
        )
        rows = cur.fetchall()
    assert rows == [(900000002, "NO MATCH"), (900000003, "CROSS-TAG")]


@pytest.mark.db
@responses.activate
def test_run_with_no_parseable_rows_still_logs_ok_with_a_note(clean_db):
    responses.add(responses.GET, supgp_crosstags.URL, body=FIXTURE_NO_ANOMALIES.read_text(), status=200)

    n = supgp_crosstags.run(clean_db)

    assert n == 0
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status, notes FROM ingest_run WHERE source = %s AND endpoint = %s "
            "ORDER BY ingest_run_id DESC LIMIT 1",
            (supgp_crosstags.SOURCE, supgp_crosstags.ENDPOINT),
        )
        status, notes = cur.fetchone()
    assert status == "ok"
    assert notes and "no parseable" in notes
