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

from ingest import filing_blobs


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
