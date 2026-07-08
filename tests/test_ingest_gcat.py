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
    assert len(raw_rows) == 3
    # Header keys come from the file, not a hardcoded position list.
    assert set(raw_rows[0]) == {
        "JCAT", "Satcat", "Piece", "Type", "Name", "PLName", "LDate", "DDate", "Status",
        "Dest", "Owner", "State", "Manufacturer", "Bus", "Mass", "Perigee", "Apogee", "Inc",
        "OpOrbit", "AltNames", "ODate",
    }


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


@pytest.mark.db
@responses.activate
def test_run_lands_both_tsvs(clean_db, monkeypatch, tmp_path):
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
