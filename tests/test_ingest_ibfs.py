"""Tests for the FCC IBFS bulk-dump loader.

The fixture strings replicate the dump format exactly as observed live on 2026-07-30, not as
the 1998 ibfs.txt describes it: records terminated by '|^|' + CRLF, embedded CRLFs inside MAIN
text fields, undocumented trailing columns after the documented layout (the live MAIN carries
~71 fields against 53 documented), dashless file numbers, Sybase datetimes with a double space
before one-digit day/hour, cp1252 bytes, and the space-station table exported with literal
duplicate rows. The filing fixture mirrors a real pending application's shape with contact
details genericized.
"""

import datetime as dt
import decimal
import io
import zipfile

import pytest

from ingest import ibfs

# A pending SAT filing (filed, no grant/deny/dismiss/surrender), full live-record shape:
# documented columns in positions 0-52, then the undocumented post-1998 tail (positions 53+,
# including the FRN-looking value at position 61) which the parser must ignore.
PENDING_FILING = (
    "-525566|0|S3236|SATLOA2025061800152|SAT|FAFV|Jun 18 2025  4:50:24:023PM||||"
    "Jun 18 2025  4:50:22:490PM||||||||Jun 18 2025  4:50:24:223PM|||||IC2025003246| |LOA"
    "||||||||COR||SSG||Jane Doe|Head of Regulatory||Authority to provide FSS, including "
    "ESIMs, from the 157E orbit location in Ka band frequencies|-525777|Contact Name|"
    "555-0100||contact@example.com|-525778||Same|555-0101||contact2@example.com|||||"
    "Jun 18 2025  4:50:22:490PM|||||0032990756|||||||||"
)

# A granted modification whose description contains an embedded CRLF, which is why records
# split on the full terminator and never on lines.
GRANTED_FILING = (
    "-1499997665|2|S2110|SATMOD1996120400139|SAT|COR|Jun 15 2022 10:03:09:466AM|GRA|"
    "Feb  4 2013 11:31:30:783AM||Dec  4 1996 12:00:00:000AM|Dec  4 1996 12:00:00:000AM|"
    "Feb  4 2013 11:31:30:783AM||||||Jun 15 2022 10:03:10:960AM||||||8210119-1|MOD"
    "||||||||||SSN|||||Modification of authorization\r\nfor the IRIDIUM system|123||||||||||||"
)

FILING_BLOB = PENDING_FILING + "|^|\r\n" + GRANTED_FILING + "|^|\r\n"

# Real space-station shapes: the same row exported twice back to back (the dump does this at
# scale), a call sign embedded in parentheses, a cp1252 degree sign, and blank-padded cells.
SPACE_STATION_BLOB = (
    "889170|O3B-A (S2935)|O3B-A|NGSO|O3b Limited NGSO satellite system (S2935) (UK-licensed)|"
    "May 13 2025 12:00:00:000AM||^|\r\n"
    "889170|O3B-A (S2935)|O3B-A|NGSO|O3b Limited NGSO satellite system (S2935) (UK-licensed)|"
    "May 13 2025 12:00:00:000AM||^|\r\n"
    "-2099999994|YAM-2 NGSO (S3052)|YAM-2 NGSO Satellite|NGSO|YAM-2 NGSO (S3052)satelite "
    "@ 450-550 km@97.5° inclination|Oct  8 2035 12:00:00:000AM||^|\r\n"
    "238|PERMITTED LIST| | |Permitted Space Station List|Dec 31 2099 12:00:00:000AM| |^|\r\n"
)

FREQUENCY_BLOB = (
    "-2099989489|-2099989492|Z|||36000F9|00003700.00000000|00004200.00000000|R|||||^|\r\n"
    "-2099989465|-2099989469|Z|43.299999999999997|31.300000000000001|100F9Y|"
    "00005925.00000000|00006105.00000000|T|||||^|\r\n"
)


def test_parse_records_filings_real_format():
    rows = ibfs.parse_records(FILING_BLOB, ibfs._FILING_SPEC, "raw_ibfs_filings")
    assert len(rows) == 2

    pending, granted = rows
    assert pending["filing_key"] == -525566
    assert pending["callsign"] == "S3236"
    assert pending["file_number"] == "SATLOA2025061800152"  # dashless, as the dump stores it
    assert pending["subsystem_code"] == "SAT"
    assert pending["status_code"] == "FAFV"
    assert pending["app_type_code"] == "LOA"
    assert pending["class_of_station_code"] == "SSG"
    assert pending["date_filed"] == dt.date(2025, 6, 18)
    assert pending["date_grant"] is None
    assert pending["date_deny"] is None
    assert pending["description"].startswith("Authority to provide FSS")
    # Only spec columns come back; the undocumented tail (FRN at position 61 etc.) is ignored.
    assert set(pending) == {c for c, _, _ in ibfs._FILING_SPEC}

    assert granted["date_filed"] == dt.date(1996, 12, 4)
    assert granted["date_grant"] == dt.date(2013, 2, 4)
    assert granted["last_action"] == "GRA"
    # The embedded CRLF stays inside the field instead of splitting the record.
    assert "\r\n" in granted["description"]


def test_parse_records_space_stations_keeps_duplicates_and_cp1252_text():
    rows = ibfs.parse_records(
        SPACE_STATION_BLOB, ibfs._SPACE_STATION_SPEC, "raw_ibfs_space_stations"
    )
    assert len(rows) == 4  # raw layer lands the dump's literal duplicates as published
    assert rows[0] == rows[1]
    assert rows[0]["space_station_key"] == 889170
    assert rows[0]["us_name"] == "O3B-A (S2935)"
    assert rows[0]["inactive_date"] == dt.date(2025, 5, 13)
    assert "97.5°" in rows[2]["verbose_name"]
    assert rows[3]["itu_name"] is None  # blank-padded char cells degrade to NULL


def test_parse_records_frequencies_types():
    rows = ibfs.parse_records(FREQUENCY_BLOB, ibfs._FREQUENCY_SPEC, "raw_ibfs_frequencies")
    assert len(rows) == 2
    assert rows[0]["frequency_key"] == -2099989489
    assert rows[0]["antenna_key"] == -2099989492
    assert rows[0]["eirp"] is None
    assert rows[0]["frequency_lower"] == decimal.Decimal("3700")
    assert rows[0]["trans_mode"] == "R"
    assert rows[1]["eirp"] == pytest.approx(43.3)
    assert rows[1]["frequency_upper"] == decimal.Decimal("6105")
    assert rows[1]["modulation"] is None


def test_coerce_degrades_to_null_never_raises():
    assert ibfs._coerce("t", "c", "date", "not a date") is None
    assert ibfs._coerce("t", "c", "date", "Feb 30 2020 12:00:00:000AM") is None
    assert ibfs._coerce("t", "c", "int", "12x") is None
    assert ibfs._coerce("t", "c", "float", "4..2") is None
    assert ibfs._coerce("t", "c", "numeric", "12,3") is None
    assert ibfs._coerce("t", "c", "text", "") is None
    assert ibfs._coerce("t", "c", "date", "Jan  1 1900 12:00:00:000AM") == dt.date(1900, 1, 1)
    assert ibfs._coerce("t", "c", "int", "-525566") == -525566


def test_parse_records_short_record_degrades_to_nulls():
    blob = "-1|0|S9999|SATLOA2099010100001|SAT|^|\r\n"
    rows = ibfs.parse_records(blob, ibfs._FILING_SPEC, "raw_ibfs_filings")
    assert len(rows) == 1
    assert rows[0]["filing_key"] == -1
    assert rows[0]["subsystem_code"] == "SAT"
    assert rows[0]["date_filed"] is None
    assert rows[0]["description"] is None


def test_parse_zip_decodes_cp1252_members():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("main.dat", FILING_BLOB.encode("cp1252"))
        zf.writestr("space_sta.dat", SPACE_STATION_BLOB.encode("cp1252"))
        zf.writestr("freq.dat", FREQUENCY_BLOB.encode("cp1252"))
        zf.writestr("anten.dat", b"ignored|^|\r\n")  # non-landed members are skipped
    tables = ibfs.parse_zip(buf.getvalue())
    assert set(tables) == {"raw_ibfs_filings", "raw_ibfs_space_stations", "raw_ibfs_frequencies"}
    assert len(tables["raw_ibfs_filings"]) == 2
    assert "97.5°" in tables["raw_ibfs_space_stations"][2]["verbose_name"]


@pytest.mark.db
def test_pending_view_exists_and_pending_rows_have_no_grant_date(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('v_fcc_pending_applications')")
        assert cur.fetchone()[0] is not None, "migration 0012 not applied"
        cur.execute("SELECT filing_key, file_number, date_filed FROM v_fcc_pending_applications")
        rows = cur.fetchall()
        if not rows:
            pytest.skip("no IBFS data loaded in this database")
        assert all(r[2] is not None for r in rows), "pending rows must have a filed date"
        assert all(r[1].startswith("SAT") for r in rows)
        # Re-check against the raw table: nothing the view calls pending carries a grant date.
        cur.execute(
            """
            SELECT count(*) FROM raw_ibfs_filings f
            WHERE f.filing_key = ANY(%s)
              AND f.ingest_run_id = (
                  SELECT max(r.ingest_run_id) FROM raw_ibfs_filings r
                  JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
                  WHERE i.status = 'ok')
              AND f.date_grant IS NOT NULL
            """,
            ([r[0] for r in rows],),
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_pending_filings_endpoint_contract(db_conn):
    """The pre-launch pipeline surface: pending rows are undecided by definition, newest first,
    and the search narrows without erroring."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    r = client.get("/api/filings/pending?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 0 and isinstance(body["rows"], list)
    if not body["rows"]:
        pytest.skip("no pending applications loaded in this database")
    for row in body["rows"]:
        # Pending is enforced by the view definition (no grant/denial/dismissal/surrender
        # date), so the contract here is shape: a filing key, a file number and a filed date.
        assert row["file_number"]
        assert "date_filed" in row and "satellite_name" in row
    r2 = client.get("/api/filings/pending?q=SPACEX")
    assert r2.status_code == 200
    assert r2.json()["total"] <= body["total"] or body["total"] == 0
