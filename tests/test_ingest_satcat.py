"""CelesTrak SATCAT bulk CSV loader tests: header-name parsing + full land-into-DB round trip."""

import datetime as dt
from pathlib import Path

import pytest
import responses

from ingest import celestrak_satcat
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

FIXTURE = Path(__file__).parent / "fixtures" / "satcat_sample.csv"


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


def test_parse_rows_types_by_header_name_and_nulls_empty_strings():
    rows = celestrak_satcat.parse_rows(FIXTURE.read_text())

    assert len(rows) == 5

    active = next(r for r in rows if r["norad_cat_id"] == 900000001)
    assert active["object_name"] == "OEI-TEST-SAT-1"
    assert active["launch_date"] == dt.date(2020, 1, 5)
    assert active["decay_date"] is None  # empty string -> NULL
    assert active["period"] == pytest.approx(95.2)
    assert isinstance(active["norad_cat_id"], int)

    decayed = next(r for r in rows if r["norad_cat_id"] == 900000003)
    assert decayed["decay_date"] == dt.date(2022, 3, 15)
    assert decayed["ops_status_code"] == "D"

    sparse = next(r for r in rows if r["norad_cat_id"] == 900000004)
    assert sparse["owner"] is None
    assert sparse["ops_status_code"] is None
    assert sparse["launch_site"] is None
    assert sparse["rcs"] is None


@pytest.mark.db
@responses.activate
def test_run_lands_rows_into_raw_satcat(clean_db, monkeypatch, tmp_path):
    responses.add(
        responses.GET,
        celestrak_satcat.URL,
        body=FIXTURE.read_text(),
        status=200,
    )
    monkeypatch.setattr(celestrak_satcat, "DATA_DIR", tmp_path)  # avoid touching repo data/

    n = celestrak_satcat.run(clean_db)

    assert n == 5
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT norad_cat_id, object_name, decay_date, ingest_run_id FROM raw_satcat "
            "WHERE norad_cat_id BETWEEN 900000001 AND 900000005 ORDER BY norad_cat_id"
        )
        rows = cur.fetchall()
    assert [r[0] for r in rows] == [900000001, 900000002, 900000003, 900000004, 900000005]
    assert rows[2][2] == dt.date(2022, 3, 15)
    run_id = rows[0][3]

    with clean_db.cursor() as cur:
        cur.execute("SELECT status, rows_ingested FROM ingest_run WHERE ingest_run_id = %s", (run_id,))
        status, rows_ingested = cur.fetchone()
    assert status == "ok"
    assert rows_ingested == 5


@pytest.mark.db
@responses.activate
def test_run_skips_when_fresh(clean_db, monkeypatch, tmp_path):
    monkeypatch.setattr(celestrak_satcat, "DATA_DIR", tmp_path)
    responses.add(responses.GET, celestrak_satcat.URL, body=FIXTURE.read_text(), status=200)

    first = celestrak_satcat.run(clean_db)
    assert first == 5
    assert len(responses.calls) == 1

    second = celestrak_satcat.run(clean_db)
    assert second == 0
    assert len(responses.calls) == 1  # no new HTTP call on the fresh re-run
