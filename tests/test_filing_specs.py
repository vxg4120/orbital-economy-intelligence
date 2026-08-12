"""Blob cache and citation validation for machine-derived Schedule S specs.

These pin the two properties the extraction layer's credibility rests on: that a document's
content hash is stable (so a citation stays checkable after the FCC reissues an attachment), and
that a cited page genuinely carries the value cited to it. The second is the load-bearing one. A
missing altitude is a coverage gap; a plausible citation pointing at a page that does not support
it discredits every other cited figure on the platform, so the validator is tested against the
specific coincidence that would let one through.
"""

import hashlib
import io

import pytest

from ingest import filing_blobs, spec_validate


@pytest.fixture
def two_page_pdf() -> bytes:
    """A real two-page PDF, built in-memory so the test needs no fixture file or network."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_sha256_is_stable_and_hex():
    assert filing_blobs.sha256_bytes(b"abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(filing_blobs.sha256_bytes(b"")) == 64


def test_sha256_changes_when_the_document_changes():
    """The point of storing the hash: a reissued attachment must be detectable, not silent."""
    assert filing_blobs.sha256_bytes(b"Apogee 525.0 km") != filing_blobs.sha256_bytes(
        b"Apogee 530.0 km"
    )


def test_page_texts_returns_one_entry_per_physical_page(two_page_pdf):
    pages = filing_blobs.page_texts(two_page_pdf)
    assert len(pages) == 2
    assert all(isinstance(p, str) for p in pages)


def test_looks_complete_accepts_a_whole_pdf(two_page_pdf):
    assert filing_blobs.looks_complete(two_page_pdf) is True


def test_looks_complete_rejects_a_truncated_transfer(two_page_pdf):
    """The real failure: the gateway answers 200 with content-type application/pdf and sends only
    the opening bytes. Every document of SATLOA2016062200058 arrives as 455 bytes against a
    declared length of 87,684. Those bytes ARE a valid PDF opening, so a header check alone passes
    them; the EOF marker is what catches it."""
    truncated = two_page_pdf[:455]
    assert truncated.startswith(b"%PDF-")          # a header check alone would be fooled
    assert filing_blobs.looks_complete(truncated) is False


def test_looks_complete_rejects_something_that_is_not_a_pdf():
    assert filing_blobs.looks_complete(b"<html>Access Denied</html>") is False
    assert filing_blobs.looks_complete(b"") is False


# ---------------------------------------------------------------------------------------------
# citation validation
# ---------------------------------------------------------------------------------------------


def test_page_supports_accepts_the_value_actually_present():
    assert spec_validate.page_supports("Apogee 525.0 km", 525.0) is True


def test_page_supports_accepts_the_other_rendering_of_the_same_number():
    """525 and 525.0 are the same fact. Rejecting one would fail valid rows for a formatting
    difference, and a validator that cries wolf gets switched off."""
    assert spec_validate.page_supports("Apogee 525 km", 525.0) is True
    assert spec_validate.page_supports("Total 25", 25) is True


def test_page_supports_rejects_a_value_the_page_does_not_carry():
    assert spec_validate.page_supports("Apogee 525.0 km", 1030.0) is False


def test_page_supports_rejects_substring_coincidence():
    """The whole reason this is token-anchored. 25 must not validate against "525.0", and 30 must
    not validate against "1030.0" -- a naive substring check passes both, and that is precisely how
    a citation ends up pointing at a page that does not support it."""
    assert spec_validate.page_supports("Apogee 525.0 km", 25) is False
    assert spec_validate.page_supports("Apogee 1030.0 km", 30) is False
    assert spec_validate.page_supports("Inclination Angle 97.5 degrees", 7.5) is False


def test_page_supports_handles_strings_too():
    assert spec_validate.page_supports("Network Name Landmapper", "Landmapper") is True
    assert spec_validate.page_supports("Network Name Landmapper", "Starlink") is False


def test_page_supports_refuses_a_null_value():
    """An absent field can never be 'supported'. Validating None would let abstentions through as
    if they had been confirmed."""
    assert spec_validate.page_supports("anything at all", None) is False


def test_validate_rows_flags_only_the_bad_row():
    pages = ["Apogee 525.0 km", "Apogee 1030.0 km"]
    rows = [
        {"apogee_km": 525.0, "source_page": 1},
        {"apogee_km": 999.0, "source_page": 2},
    ]
    assert spec_validate.validate_rows(pages, rows, ("apogee_km",)) == [True, False]


def test_validate_rows_rejects_a_citation_pointing_off_the_end_of_the_document():
    pages = ["Apogee 525.0 km"]
    rows = [{"apogee_km": 525.0, "source_page": 7}]
    assert spec_validate.validate_rows(pages, rows, ("apogee_km",)) == [False]


def test_validate_rows_rejects_a_row_with_nothing_to_check():
    """A row where every field of interest is null has not been validated, it has been skipped.
    Returning True there would mark empty rows publishable."""
    pages = ["Apogee 525.0 km"]
    rows = [{"apogee_km": None, "source_page": 1}]
    assert spec_validate.validate_rows(pages, rows, ("apogee_km",)) == [False]


def test_validate_rows_requires_every_present_field_to_be_supported():
    """One good field does not carry a bad one: a row is publishable only if all of it checks out."""
    pages = ["Apogee 525.0 km Perigee 525.0 km"]
    rows = [{"apogee_km": 525.0, "perigee_km": 611.0, "source_page": 1}]
    assert spec_validate.validate_rows(pages, rows, ("apogee_km", "perigee_km")) == [False]


PLANE_PAGES = {
    "apogee_km": "apogee_page",
    "perigee_km": "perigee_page",
    "inclination_deg": "inclination_page",
}


def test_fieldwise_validation_handles_a_plane_split_across_a_page_break():
    """The real-corpus case: inclination on one page, apogee and perigee on the next. A row-level
    citation fails this legitimately-correct parse; per-field citation passes it. Measured at 17 of
    74 planes on SATAMD2017030100030, so this is the common case, not an edge case."""
    pages = ["Inclination Angle 88.0 degrees", "Apogee 1030.0 km\nPerigee 1030.0 km"]
    row = {
        "inclination_deg": 88.0, "inclination_page": 1,
        "apogee_km": 1030.0, "apogee_page": 2,
        "perigee_km": 1030.0, "perigee_page": 2,
        "source_page": 1,
    }
    assert spec_validate.validate_fieldwise(pages, [row], PLANE_PAGES) == [True]
    # The old row-level check would reject this correct parse, which is what surfaced the bug.
    assert spec_validate.validate_rows(pages, [row], tuple(PLANE_PAGES)) == [False]


def test_fieldwise_validation_still_catches_a_wrong_value():
    pages = ["Inclination Angle 88.0 degrees", "Apogee 1030.0 km\nPerigee 1030.0 km"]
    row = {
        "inclination_deg": 88.0, "inclination_page": 1,
        "apogee_km": 9999.0, "apogee_page": 2,      # not on page 2
        "source_page": 1,
    }
    assert spec_validate.validate_fieldwise(pages, [row], PLANE_PAGES) == [False]


def test_fieldwise_validation_rejects_a_field_citing_a_page_that_does_not_exist():
    pages = ["Inclination Angle 88.0 degrees"]
    row = {"inclination_deg": 88.0, "inclination_page": 9, "source_page": 1}
    assert spec_validate.validate_fieldwise(pages, [row], PLANE_PAGES) == [False]


def test_fieldwise_validation_rejects_a_row_with_nothing_checked():
    pages = ["Inclination Angle 88.0 degrees"]
    assert spec_validate.validate_fieldwise(pages, [{"source_page": 1}], PLANE_PAGES) == [False]


def test_validator_confirms_a_real_parsed_row_end_to_end():
    """The integration that matters: parse a real page, then independently re-check the parse."""
    from ingest import schedule_s

    page = ("Inclination Angle 97.5 degrees\nArgument of Perigee 0.0 degrees\n"
            "Apogee 525.0 km\nPerigee 525.0 km\n")
    planes = schedule_s.parse_planes([page])
    verdicts = spec_validate.validate_rows(
        [page], planes, ("apogee_km", "perigee_km", "inclination_deg")
    )
    assert verdicts == [True]


# ---------------------------------------------------------------------------------------------
# the served surface
# ---------------------------------------------------------------------------------------------


def _client():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def test_spec_endpoint_shape_and_citations(db_conn):
    """Every served plane must carry a resolvable page citation. A row without one is exactly the
    thing the validator exists to keep off the wire."""
    body = _client().get("/api/filings/SATAMD2022063000067/spec").json()
    assert body["file_number"] == "SATAMD2022063000067"
    assert body["summary"]["orbit_type"] == "NGSO"
    assert body["planes"], "expected planes for a filing known to have them"
    for plane in body["planes"]:
        assert plane["source_page"] >= 1
        if plane["apogee_km"] is not None:
            assert plane["apogee_page"] >= 1
    for band in body["bands"]:
        assert band["source_page"] >= 1
        assert "\n" not in (band["service"] or "")


def test_spec_endpoint_reports_the_source_document_hash(db_conn):
    """The citation is only checkable if the reader knows which bytes it refers to."""
    body = _client().get("/api/filings/SATAMD2022063000067/spec").json()
    assert len(body["source_document"]["sha256"]) == 64
    assert body["source_document"]["page_count"] > 0


def test_spec_endpoint_flags_as_filed_sentinels_rather_than_correcting_them(db_conn):
    """Astrobotic files apogee 99999 because Schedule S cannot express a lunar trajectory. The
    number must be served as filed and flagged, never silently repaired, or the citation would
    point at a page that disagrees with us."""
    body = _client().get("/api/filings/SATSTA2021020800018/spec").json()
    flagged = [p for p in body["planes"] if p["as_filed_implausible"]]
    assert flagged, "expected the lunar sentinel to be flagged"
    assert flagged[0]["apogee_km"] == 99999


def test_spec_endpoint_is_empty_not_404_for_a_filing_with_no_schedule_s(db_conn):
    """Absence of a Schedule S is a coverage fact, not an error. 65 of 136 filings have one."""
    r = _client().get("/api/filings/SATLOA2016062200058/spec")
    assert r.status_code == 200
    assert r.json()["planes"] == []
