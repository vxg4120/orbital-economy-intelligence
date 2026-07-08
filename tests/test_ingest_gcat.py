"""GCAT TSV loader tests: `#`-prefixed header parsed dynamically, `-` -> NULL, missing Satcat
lands with norad_id NULL and the row's other fields preserved in `extra`."""

from pathlib import Path

import pytest
import responses

from ingest import gcat_loader
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

SATCAT_FIXTURE = Path(__file__).parent / "fixtures" / "gcat_satcat_sample.tsv"
PSATCAT_FIXTURE = Path(__file__).parent / "fixtures" / "gcat_psatcat_sample.tsv"


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


def test_parse_tsv_reads_hash_prefixed_header_dynamically():
    raw_rows = gcat_loader.parse_tsv(SATCAT_FIXTURE.read_text())
    # The "# Updated ..." banner line after the header is a comment, not a data row.
    assert len(raw_rows) == 3
    # Header keys come from the file, not a hardcoded position list.
    assert set(raw_rows[0]) == {
        "JCAT", "Satcat", "Piece", "Type", "Name", "PLName", "LDate", "DDate", "Status",
        "Dest", "Owner", "State", "Manufacturer", "Bus", "Mass", "Perigee", "Apogee", "Inc",
        "OpOrbit", "AltNames", "ODate",
    }


def test_parse_tsv_skips_update_banner_comment_line():
    """The real GCAT files carry a '# Updated <date>' line under the header; it must never land
    as a bogus one-column row (JCAT ids never start with '#')."""
    raw_rows = gcat_loader.parse_tsv(SATCAT_FIXTURE.read_text())
    assert all(not row["JCAT"].startswith("#") for row in raw_rows)


def test_coerce_satcat_types_is_defensive_on_bad_numeric():
    """A single unparseable numeric in a 40k-row live pull must degrade to NULL (preserved in
    extra) instead of aborting the whole landing transaction."""
    bad = "#JCAT\tSatcat\tPerigee\tApogee\tInc\n" "S99999\t50000\t550\tNOT_A_NUMBER\t53.0\n"
    typed, extra = gcat_loader.process_satcat_rows(gcat_loader.parse_tsv(bad))[0]
    assert typed["norad_id"] == 50000
    assert typed["perigee_km"] == pytest.approx(550)
    assert typed["apogee_km"] is None  # unparseable -> NULL
    assert extra["_unparsed_apogee_km"] == "NOT_A_NUMBER"  # raw value preserved, nothing lost


def test_process_satcat_rows_maps_types_nulls_dashes_and_keeps_extra():
    processed = gcat_loader.process_satcat_rows(gcat_loader.parse_tsv(SATCAT_FIXTURE.read_text()))
    by_norad = {typed.get("norad_id"): (typed, extra) for typed, extra in processed}

    typed, extra = by_norad[900000001]
    assert typed["jcat"] == "2020-001A"
    assert typed["decay_date"] is None  # '-' -> NULL
    assert typed["perigee_km"] == pytest.approx(550)
    assert typed["apogee_km"] == pytest.approx(560)
    assert extra["ODate"] == "2020 Jan  6"  # unmapped column preserved verbatim
    assert typed["alt_names"] is None  # '-' -> NULL, even for a mapped typed column


def test_process_satcat_rows_missing_satcat_number_is_null_norad():
    processed = gcat_loader.process_satcat_rows(gcat_loader.parse_tsv(SATCAT_FIXTURE.read_text()))
    analyst = next(typed for typed, _ in processed if typed.get("jcat") == "OEI-ANALYST-1")
    assert analyst.get("norad_id") is None


@pytest.fixture
def _unique_gcat_endpoints(monkeypatch):
    """Unique endpoint tags so the real 'gcat_satcat'/'gcat_psatcat' freshness ledger rows on the
    shared dev DB never skip these pulls; cleanup_since removes the runs + rows afterwards."""
    monkeypatch.setattr(gcat_loader, "SATCAT_ENDPOINT", "gcat_satcat_test")
    monkeypatch.setattr(gcat_loader, "PSATCAT_ENDPOINT", "gcat_psatcat_test")


@pytest.mark.db
@responses.activate
def test_run_lands_both_tsvs(clean_db, monkeypatch, tmp_path, _unique_gcat_endpoints):
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    responses.add(responses.GET, gcat_loader.SATCAT_URL, body=SATCAT_FIXTURE.read_text(), status=200)
    responses.add(responses.GET, gcat_loader.PSATCAT_URL, body=PSATCAT_FIXTURE.read_text(), status=200)

    counts = gcat_loader.run(clean_db)

    assert counts == {"satcat": 3, "psatcat": 2}

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT norad_id, name, extra FROM raw_gcat_satcat WHERE jcat = 'OEI-ANALYST-1'"
        )
        norad_id, name, extra = cur.fetchone()
    assert norad_id is None
    assert name == "OEI-TEST-ANALYST"
    assert extra == {"ODate": None}  # unmapped column preserved, dash normalized to NULL

    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_gcat_psatcat WHERE jcat LIKE '2020-00%A'")
        (count,) = cur.fetchone()
    assert count == 2


@pytest.mark.db
@responses.activate
def test_run_twice_skips_both_endpoints_when_fresh(clean_db, monkeypatch, tmp_path,
                                                    _unique_gcat_endpoints):
    """Finding #9: a second run() within the freshness window issues zero new HTTP requests for
    BOTH the satcat and psatcat legs."""
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    responses.add(responses.GET, gcat_loader.SATCAT_URL, body=SATCAT_FIXTURE.read_text(), status=200)
    responses.add(responses.GET, gcat_loader.PSATCAT_URL, body=PSATCAT_FIXTURE.read_text(), status=200)

    first = gcat_loader.run(clean_db)
    assert first == {"satcat": 3, "psatcat": 2}
    assert len(responses.calls) == 2  # one GET per endpoint

    second = gcat_loader.run(clean_db)
    assert second == {"satcat": 0, "psatcat": 0}
    assert len(responses.calls) == 2  # no new HTTP calls on the fresh re-run
