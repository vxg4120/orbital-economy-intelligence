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
    # Unique group keeps run() off the real 'gp_active' freshness ledger row on the shared dev DB
    # (a background GP job's fresh run would otherwise make this run() skip and land 0 rows).
    group = "testland"
    responses.add(responses.GET, _gp_url(group), body=FIXTURE.read_text(), status=200)

    n = celestrak_gp.run(clean_db, group=group)

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
    # Unique group so the first run() actually fetches instead of skipping on the shared dev DB's
    # live 'gp_active' freshness row (see test_run_lands_gp_rows).
    group = "testdup"
    responses.add(responses.GET, _gp_url(group), body=FIXTURE.read_text(), status=200)

    celestrak_gp.run(clean_db, group=group)
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


@pytest.mark.db
@responses.activate
def test_run_skips_when_fresh(clean_db):
    """Finding #9: a second run() within the 2h freshness window issues zero new HTTP requests,
    exercising celestrak_gp.run()'s own skip path end-to-end (a unique group keeps this off the
    real 'gp_active' ledger row on the shared dev DB)."""
    group = "testfresh"
    responses.add(responses.GET, _gp_url(group), body=FIXTURE.read_text(), status=200)

    first = celestrak_gp.run(clean_db, group=group)
    assert first == 3
    assert len(responses.calls) == 1

    second = celestrak_gp.run(clean_db, group=group)
    assert second == 0
    assert len(responses.calls) == 1  # no new HTTP call within the 2h freshness window


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


@pytest.mark.db
def test_land_gp_rows_skips_stub_rows_missing_required_keys(db_conn):
    """Observed live: Space-Track under load can return degraded stub rows (missing
    NORAD_CAT_ID). One bad row must not kill a multi-hour backfill — skip and count."""
    good = dict(FIXTURE_ROWS[0], NORAD_CAT_ID=900000601)
    stub_no_norad = {k: v for k, v in FIXTURE_ROWS[1].items() if k != "NORAD_CAT_ID"}
    stub_no_epoch = dict(
        {k: v for k, v in FIXTURE_ROWS[2].items() if k != "EPOCH"}, NORAD_CAT_ID=900000603
    )

    landed = celestrak_gp.land_gp_rows(
        db_conn, [good, stub_no_norad, stub_no_epoch], source="test_stub_skip"
    )
    assert landed == 1
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM gp_elements WHERE source = 'test_stub_skip'")
        assert cur.fetchone()[0] == 1
        cur.execute("DELETE FROM gp_elements WHERE source = 'test_stub_skip'")
    db_conn.commit()
