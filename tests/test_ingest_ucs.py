"""UCS seed loader tests: local-file landing + graceful no-op when nothing is available."""

from pathlib import Path

import pytest

from ingest import ucs_seed
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

FIXTURE = Path(__file__).parent / "fixtures" / "ucs_sample.txt"


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


def test_parse_rows_maps_headers_and_hashes_row_key():
    rows = ucs_seed.parse_rows(FIXTURE.read_text())
    assert len(rows) == 3

    typed, extra = rows[0]
    assert typed["name"] == "OEI Test Sat 1"
    assert typed["norad_id"] == 900000001
    assert typed["cospar_id"] == "2020-001A"
    assert len(typed["row_key"]) == 40  # sha1 hexdigest
    assert extra == {}

    analyst_typed, _ = rows[2]
    assert analyst_typed["norad_id"] is None  # blank NORAD Number cell


@pytest.mark.db
def test_run_with_explicit_path_lands_rows(clean_db):
    n = ucs_seed.run(clean_db, path_or_url=str(FIXTURE))

    assert n == 3
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT norad_id, name FROM raw_ucs WHERE norad_id BETWEEN 900000001 AND 900000002 "
            "ORDER BY norad_id"
        )
        rows = cur.fetchall()
    assert rows == [(900000001, "OEI Test Sat 1"), (900000002, "OEI Test Sat 2")]


def test_run_with_no_file_and_no_url_is_a_graceful_noop(db_conn, monkeypatch):
    monkeypatch.setattr(ucs_seed, "LOCAL_GLOBS", ("data/ucs-does-not-exist/*.txt",))
    n = ucs_seed.run(db_conn, path_or_url=None)
    assert n == 0


def test_run_rejects_xlsx_with_a_clear_error(db_conn, tmp_path):
    fake_xlsx = tmp_path / "ucs.xlsx"
    fake_xlsx.write_bytes(b"not really an xlsx")
    with pytest.raises(ValueError, match="xlsx"):
        ucs_seed.run(db_conn, path_or_url=str(fake_xlsx))
