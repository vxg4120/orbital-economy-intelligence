"""GCAT TSV loader tests: `#`-prefixed header parsed dynamically, `-` -> NULL, missing Satcat
lands with norad_id NULL and the row's other fields preserved in `extra`."""

from pathlib import Path

import pytest
import requests
import responses

from ingest import gcat_loader
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

SATCAT_FIXTURE = Path(__file__).parent / "fixtures" / "gcat_satcat_sample.tsv"
PSATCAT_FIXTURE = Path(__file__).parent / "fixtures" / "gcat_psatcat_sample.tsv"
# Part 3 of each catalog (NORAD 100000 and above), which GCAT serves as a second file.
SATCAT_100K_FIXTURE = Path(__file__).parent / "fixtures" / "gcat_satcat100k_sample.tsv"
PSATCAT_100K_FIXTURE = Path(__file__).parent / "fixtures" / "gcat_psatcat100k_sample.tsv"


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
    assert typed["jcat"] == "S900000001"
    assert typed["decay_date"] is None  # '-' -> NULL
    assert typed["perigee_km"] == pytest.approx(550)
    assert typed["apogee_km"] == pytest.approx(560)
    assert extra["ODate"] == "2020 Jan  6"  # unmapped column preserved verbatim
    assert typed["alt_names"] is None  # '-' -> NULL, even for a mapped typed column


def test_process_satcat_rows_missing_satcat_number_is_null_norad():
    processed = gcat_loader.process_satcat_rows(gcat_loader.parse_tsv(SATCAT_FIXTURE.read_text()))
    analyst = next(typed for typed, _ in processed if typed.get("jcat") == "S900000099")
    assert analyst.get("norad_id") is None


@pytest.fixture
def _unique_gcat_endpoints(monkeypatch):
    """Unique endpoint tags so the real 'gcat_satcat'/'gcat_psatcat' freshness ledger rows on the
    shared dev DB never skip these pulls; cleanup_since removes the runs + rows afterwards."""
    monkeypatch.setattr(gcat_loader, "SATCAT_ENDPOINT", "gcat_satcat_test")
    monkeypatch.setattr(gcat_loader, "PSATCAT_ENDPOINT", "gcat_psatcat_test")


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """A failed part is retried after real waits; the tests never sleep."""
    monkeypatch.setattr(gcat_loader.runlog, "_sleep", lambda _seconds: None)


def _serve(satcat_100k=None, psatcat_100k=None):
    """Register all four GCAT files. Part 3 defaults to its fixture; pass a body or a status."""
    responses.add(responses.GET, gcat_loader.SATCAT_URL, body=SATCAT_FIXTURE.read_text(), status=200)
    responses.add(responses.GET, gcat_loader.PSATCAT_URL, body=PSATCAT_FIXTURE.read_text(), status=200)
    for url, part, fixture in (
        (gcat_loader.SATCAT_100K_URL, satcat_100k, SATCAT_100K_FIXTURE),
        (gcat_loader.PSATCAT_100K_URL, psatcat_100k, PSATCAT_100K_FIXTURE),
    ):
        if isinstance(part, int):
            responses.add(responses.GET, url, body="unavailable", status=part)
        else:
            responses.add(responses.GET, url, body=part or fixture.read_text(), status=200)


def _runs(conn, endpoint):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ingest_run_id, status, rows_ingested, bytes_downloaded, notes FROM ingest_run "
            "WHERE source = 'gcat' AND endpoint = %s ORDER BY ingest_run_id",
            (endpoint,),
        )
        return cur.fetchall()


def _landed_jcats(conn, table, run_id):
    with conn.cursor() as cur:
        cur.execute(f"SELECT jcat FROM {table} WHERE ingest_run_id = %s ORDER BY jcat", (run_id,))
        return [row[0] for row in cur.fetchall()]


@pytest.mark.db
@responses.activate
def test_run_lands_both_tsvs(clean_db, monkeypatch, tmp_path, _unique_gcat_endpoints):
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    _serve()

    counts = gcat_loader.run(clean_db)

    assert counts == {"satcat": 5, "psatcat": 3}

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT norad_id, name, extra FROM raw_gcat_satcat WHERE jcat = 'S900000099'"
        )
        norad_id, name, extra = cur.fetchone()
    assert norad_id is None
    assert name == "OEI-TEST-ANALYST"
    assert extra == {"ODate": None}  # unmapped column preserved, dash normalized to NULL

    with clean_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_gcat_psatcat WHERE jcat IN ('S900000001', 'S900000002')")
        (count,) = cur.fetchone()
    assert count == 2


@pytest.mark.db
@responses.activate
def test_a_pull_lands_both_parts_of_the_s_catalog_in_one_run(clean_db, monkeypatch, tmp_path,
                                                            _unique_gcat_endpoints):
    """satcat.tsv stops at S69999; everything catalogued since July 2026 is in satcat100k.tsv.
    Every reader takes the newest run as the snapshot, so both parts must be one run."""
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    _serve()

    gcat_loader.run(clean_db)

    [(run_id, status, rows, bytes_dl, _notes)] = _runs(clean_db, "gcat_satcat_test")
    assert (status, rows) == ("ok", 5)
    assert bytes_dl == len(SATCAT_FIXTURE.read_bytes()) + len(SATCAT_100K_FIXTURE.read_bytes())
    assert _landed_jcats(clean_db, "raw_gcat_satcat", run_id) == [
        "S900000001", "S900000002", "S900000099", "S900100001", "S900100002",
    ]
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT jcat, norad_id, extra->>'_unparsed_satcat' FROM raw_gcat_satcat "
            "WHERE ingest_run_id = %s AND jcat LIKE 'S9001%%' ORDER BY jcat",
            (run_id,),
        )
        assert cur.fetchall() == [("S900100001", 900100001, None), ("S900100002", None, "NNA")]
    saved = sorted(path.name.rsplit("-", 3)[0] for path in tmp_path.iterdir())
    assert saved == ["psatcat", "psatcat100k", "satcat", "satcat100k"]


@pytest.mark.db
@responses.activate
def test_the_payload_catalog_lands_both_parts_in_one_run(clean_db, monkeypatch, tmp_path,
                                                         _unique_gcat_endpoints):
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    _serve()

    gcat_loader.run(clean_db)

    [(run_id, status, rows, bytes_dl, _notes)] = _runs(clean_db, "gcat_psatcat_test")
    assert (status, rows) == ("ok", 3)
    assert bytes_dl == len(PSATCAT_FIXTURE.read_bytes()) + len(PSATCAT_100K_FIXTURE.read_bytes())
    assert _landed_jcats(clean_db, "raw_gcat_psatcat", run_id) == [
        "S900000001", "S900000002", "S900100001",
    ]


@pytest.mark.db
@responses.activate
def test_a_part_that_fails_fails_the_whole_run(clean_db, monkeypatch, tmp_path,
                                               _unique_gcat_endpoints):
    """Landing part 1 alone would make the claims pass close the GCAT claims of every
    satellite in part 3, so a failed part leaves the previous complete run as the newest."""
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    _serve(satcat_100k=404)

    with pytest.raises(requests.HTTPError):
        gcat_loader.run(clean_db)

    [(run_id, status, rows, _bytes, notes)] = _runs(clean_db, "gcat_satcat_test")
    assert (status, rows) == ("error", 0)
    assert notes.startswith(f"{gcat_loader.SATCAT_100K_URL}: ")
    assert _landed_jcats(clean_db, "raw_gcat_satcat", run_id) == []


@pytest.mark.db
@responses.activate
def test_a_duplicate_key_across_parts_fails_the_run_before_any_row_lands(
    clean_db, monkeypatch, tmp_path, _unique_gcat_endpoints
):
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    overlapping = SATCAT_100K_FIXTURE.read_text() + (
        "S900000002\t900000002\tA\tP\tOEI-TEST-SAT-2\t-\t-\t-\tO\t-\t-\t-\t-\t-\t-"
        "\t-\t-\t-\t-\t-\t-\n"
    )
    _serve(satcat_100k=overlapping)

    with pytest.raises(ValueError, match="S900000002"):
        gcat_loader.run(clean_db)

    [(run_id, status, rows, _bytes, notes)] = _runs(clean_db, "gcat_satcat_test")
    assert (status, rows) == ("error", 0)
    assert "S900000002" in notes
    assert _landed_jcats(clean_db, "raw_gcat_satcat", run_id) == []


@pytest.mark.db
@responses.activate
def test_run_twice_skips_both_endpoints_when_fresh(clean_db, monkeypatch, tmp_path,
                                                    _unique_gcat_endpoints):
    """Finding #9: a second run() within the freshness window issues zero new HTTP requests for
    BOTH the satcat and psatcat legs."""
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    _serve()

    first = gcat_loader.run(clean_db)
    assert first == {"satcat": 5, "psatcat": 3}
    assert len(responses.calls) == 4  # two files per endpoint

    second = gcat_loader.run(clean_db)
    assert second == {"satcat": 0, "psatcat": 0}
    assert len(responses.calls) == 4  # no new HTTP calls on the fresh re-run


def test_parse_parts_rejects_a_key_in_two_files_but_not_a_repeat_within_one():
    """Within one file a repeated key is the landing's business (orgs.tsv's guard is ON CONFLICT
    DO NOTHING); across the parts of a split catalog it means the split itself changed."""
    header = "#JCAT\tName\n"
    one = header + "S00001\tA\nS00001\tA again\n"
    assert len(gcat_loader.parse_parts([one])) == 2

    with pytest.raises(ValueError, match="S00001"):
        gcat_loader.parse_parts([header + "S00001\tA\n", header + "S00001\tB\nS100000\tC\n"])
    assert len(gcat_loader.parse_parts([header + "S00001\tA\n", header + "S100000\tC\n"])) == 2


def test_parse_parts_finds_the_key_column_by_name():
    """GCAT reorders columns between releases, which is why the loader maps by header name. A
    column with repeating values ahead of JCAT must not read as a duplicate key."""
    part1 = "#Launch_Tag\tJCAT\tName\n2026-160\tS00001\tA\n"
    part3 = "#Launch_Tag\tJCAT\tName\n2026-160\tS100000\tB\n"
    assert len(gcat_loader.parse_parts([part1, part3])) == 2
    with pytest.raises(ValueError, match="S00001"):
        gcat_loader.parse_parts([part1, part3 + "2026-161\tS00001\tC\n"])


@pytest.mark.parametrize("body, problem", [
    ("#JCAT\tName\n# Updated 2026 Nov 13 0021:57\n", "no data rows"),
    ("<html><body>Service unavailable</body></html>\n", "no data rows"),
    ("<html>\n<body>Service unavailable</body>\n</html>\n", "no jcat column"),
])
def test_a_part_that_is_not_a_catalog_part_fails_the_parse(body, problem):
    """A part that answers 200 with only its header, or with an error page, would otherwise
    land part 1 alone as an ok run, and the claims pass would close every claim part 3 made."""
    with pytest.raises(ValueError, match=f"satcat100k: {problem}"):
        gcat_loader.parse_parts([SATCAT_FIXTURE.read_text(), body], names=["satcat", "satcat100k"])


@pytest.mark.parametrize("body", [
    "#JCAT\tSatcat\tName\nService unavailable\n",
    "#JCAT\tSatcat\tName\nS100000\t100000\tA\n<html>error</html>\n",
    "#JCAT\tSatcat\tName\n\t100000\tA\n",
])
def test_a_jcat_part_with_any_key_that_is_not_an_s_number_fails_the_parse(body):
    """A real header followed by an error message parses into a row whose key is the message.
    Every key in GCAT's S catalog is S plus five or more digits (all 100,557 on production on
    2026-11-18), so any other key means the file is not the catalog (Codex review, 2026-11-18)."""
    with pytest.raises(ValueError, match="satcat100k: key not an S catalog number"):
        gcat_loader.parse_parts([SATCAT_FIXTURE.read_text(), body], names=["satcat", "satcat100k"])


def test_a_short_part_with_one_genuine_row_parses():
    rows = gcat_loader.parse_parts(
        [SATCAT_FIXTURE.read_text(), "#JCAT\tSatcat\tName\nS100000\t100000\tA\n"],
        names=["satcat", "satcat100k"],
    )
    assert rows[-1]["JCAT"] == "S100000"


def test_an_orgs_file_with_only_blank_codes_fails_the_parse():
    """Org codes are free text (production has "UAE AD"), so orgs needs only one real code."""
    with pytest.raises(ValueError, match="orgs: no code values"):
        gcat_loader.parse_parts(["#Code\tName\n\tA\n\tB\n"], key="code", names=["orgs"])
    assert len(gcat_loader.parse_parts(["#Code\tName\nUAE AD\tA\n"], key="code")) == 1


@pytest.mark.db
@responses.activate
def test_a_part_that_answers_200_with_only_a_header_fails_the_whole_run(
    clean_db, monkeypatch, tmp_path, _unique_gcat_endpoints
):
    monkeypatch.setattr(gcat_loader, "DATA_DIR", tmp_path)
    _serve(satcat_100k="#JCAT\tSatcat\tName\n# Updated 2026 Nov 13 0021:57\n")

    with pytest.raises(ValueError, match="satcat100k: no data rows"):
        gcat_loader.run(clean_db)

    [(run_id, status, rows, _bytes, notes)] = _runs(clean_db, "gcat_satcat_test")
    assert (status, rows) == ("error", 0)
    assert "satcat100k: no data rows" in notes
    assert _landed_jcats(clean_db, "raw_gcat_satcat", run_id) == []
