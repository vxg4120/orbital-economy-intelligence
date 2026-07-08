"""GCAT orgs.tsv loader tests: dynamic `#`-prefixed header, `-` -> NULL, unmapped columns kept in
`extra`, non-ASCII Name preserved alongside EName, landing into raw_gcat_orgs, freshness skip."""

from pathlib import Path

import pytest
import responses

from ingest import gcat_loader
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

ORGS_FIXTURE = Path(__file__).parent / "fixtures" / "gcat_orgs_sample.tsv"


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


def test_process_orgs_rows_maps_typed_subset_nulls_dashes_and_keeps_extra():
    processed = gcat_loader.process_orgs_rows(gcat_loader.parse_tsv(ORGS_FIXTURE.read_text()))
    by_code = {typed["code"]: (typed, extra) for typed, extra in processed}

    typed, extra = by_code["OEIORGA"]
    assert typed["state_code"] == "US"
    assert typed["org_class"] == "B"
    assert typed["org_type"] == "O/PL"
    assert typed["parent_code"] == "OEIORGB"
    assert typed["t_stop"] is None  # '-' -> NULL
    # Columns outside the typed subset are preserved verbatim, nothing dropped.
    assert set(extra) == {"Location", "Longitude", "Latitude", "Error", "ShortEName", "UName"}
    assert extra["Location"] == "Seattle"


def test_process_orgs_rows_preserves_non_ascii_name_and_ename():
    processed = gcat_loader.process_orgs_rows(gcat_loader.parse_tsv(ORGS_FIXTURE.read_text()))
    org = next(typed for typed, _ in processed if typed["code"] == "OEIORGB")
    assert not org["name"].isascii()  # Cyrillic Name kept as-is
    assert org["e_name"] == "Roskosmos Test Org"
    assert org["parent_code"] is None  # '-' -> NULL


def test_process_orgs_rows_drops_codeless_rows():
    bad = "#Code\tName\n-\tNo Code Org\nOK1\tHas Code\n"
    processed = gcat_loader.process_orgs_rows(gcat_loader.parse_tsv(bad))
    assert [t["code"] for t, _ in processed] == ["OK1"]


@pytest.fixture
def _unique_orgs_endpoint(monkeypatch):
    monkeypatch.setattr(gcat_loader, "ORGS_ENDPOINT", "gcat_orgs_test")


@pytest.mark.db
@responses.activate
def test_run_orgs_lands_into_raw_gcat_orgs(clean_db, monkeypatch, tmp_path, _unique_orgs_endpoint):
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    responses.add(responses.GET, gcat_loader.ORGS_URL, body=ORGS_FIXTURE.read_text(), status=200)

    n = gcat_loader.run_orgs(clean_db)
    assert n == 2

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT state_code, org_class, parent_code, e_name, extra "
            "FROM raw_gcat_orgs WHERE code = 'OEIORGA' "
            "ORDER BY ingest_run_id DESC LIMIT 1"
        )
        state, cls, parent, ename, extra = cur.fetchone()
    assert (state, cls, parent) == ("US", "B", "OEIORGB")
    assert extra["UName"] == "OEIORGA"


@pytest.mark.db
@responses.activate
def test_run_orgs_skips_when_fresh(clean_db, monkeypatch, tmp_path, _unique_orgs_endpoint):
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    responses.add(responses.GET, gcat_loader.ORGS_URL, body=ORGS_FIXTURE.read_text(), status=200)

    assert gcat_loader.run_orgs(clean_db) == 2
    assert len(responses.calls) == 1
    assert gcat_loader.run_orgs(clean_db) == 0  # fresh within MIN_INTERVAL
    assert len(responses.calls) == 1  # no new HTTP call
