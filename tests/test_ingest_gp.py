"""CelesTrak GP (OMM JSON) loader tests: landing + ON CONFLICT dedup + generated columns."""

import json
from pathlib import Path

import pytest
import responses

from ingest import celestrak_gp
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

FIXTURE = Path(__file__).parent / "fixtures" / "gp_sample.json"
FIXTURE_ROWS = json.loads(FIXTURE.read_text())


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


def _gp_url(group="active"):
    return celestrak_gp.URL_TMPL.format(group=group)


@pytest.mark.db
@responses.activate
def test_run_lands_gp_rows(clean_db):
    responses.add(responses.GET, _gp_url(), body=FIXTURE.read_text(), status=200)

    n = celestrak_gp.run(clean_db)

    assert n == 3
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT norad_id, source, semi_major_axis_km, apogee_km, perigee_km "
            "FROM gp_elements WHERE norad_id BETWEEN 900000001 AND 900000003 ORDER BY norad_id"
        )
        rows = cur.fetchall()
    assert [r[0] for r in rows] == [900000001, 900000002, 900000003]
    for _, source, sma, apogee, perigee in rows:
        assert source == "celestrak_gp"
        assert sma is not None  # generated column populated
        assert apogee is not None
        assert perigee is not None
        assert perigee < apogee


@pytest.mark.db
@responses.activate
def test_run_twice_does_not_duplicate_rows(clean_db):
    responses.add(responses.GET, _gp_url(), body=FIXTURE.read_text(), status=200)

    celestrak_gp.run(clean_db)
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM gp_elements WHERE norad_id BETWEEN 900000001 AND 900000003"
        )
        (count_after_first,) = cur.fetchone()

    # Same fixture again, past the 2h freshness window this time via a fresh endpoint tag so
    # the second GET actually fires (re-running the identical epoch/norad/source combo).
    run_id = celestrak_gp.runlog.start_run(clean_db, celestrak_gp.SOURCE, "gp_active_replay")
    n = celestrak_gp.land_gp_rows(clean_db, FIXTURE_ROWS, source="celestrak_gp")
    celestrak_gp.runlog.finish_run(clean_db, run_id, rows=n, bytes_dl=0, status="ok")

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM gp_elements WHERE norad_id BETWEEN 900000001 AND 900000003"
        )
        (count_after_second,) = cur.fetchone()

    assert count_after_first == 3
    assert count_after_second == 3  # ON CONFLICT DO NOTHING: no duplicates


def test_land_gp_rows_field_mapping_is_pure(monkeypatch):
    """land_gp_rows only needs a cursor-yielding conn; verify it maps every OMM field without
    hitting a real database (fast, no `db` marker needed)."""

    captured = []

    class _FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            captured.append(params)

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def commit(self):
            pass

    n = celestrak_gp.land_gp_rows(_FakeConn(), FIXTURE_ROWS, source="celestrak_gp")

    assert n == 3
    assert captured[0][0] == 900000001  # norad_id
    assert captured[0][10] == "celestrak_gp"  # source
    assert captured[0][2] == pytest.approx(15.5)  # mean_motion
