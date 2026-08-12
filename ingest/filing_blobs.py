"""Fetch, hash, and cache FCC filing document bytes, and extract per-page text.

Extraction has to be reproducible against bytes we hold. The api-prod gateway can change or
withdraw an attachment, and a citation that cannot be re-checked later is worth nothing on a
platform whose whole pitch is provenance. So every document is recorded with its sha256, byte
count and page count, and the parser only ever sees text derived from a blob we have hashed.

pypdf rather than poppler: the Schedule S Tech Reports render label and value on the same line
under pypdf (measured, 20 of 20 orbital pairs in the sample), so the pure-Python dependency is
sufficient and the runtime image needs no apt packages. That matters more than it looks, because
scripts/ and ingest/ are baked into the image rather than bind-mounted, and an apt-level dependency
would turn every extraction change into a base-image concern.
"""

from __future__ import annotations

import hashlib
import io
import logging

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 120


def sha256_bytes(data: bytes) -> str:
    """Content hash of a document, stored beside every field extracted from it.

    This is what makes a citation checkable after the fact: if the FCC reissues an attachment, the
    hash changes and the rows extracted from the old bytes are visibly stale rather than silently
    wrong.
    """
    return hashlib.sha256(data).hexdigest()


def looks_complete(data: bytes) -> bool:
    """True when the bytes are a whole PDF rather than a truncated transfer.

    The api-prod gateway serves some attachments partially: it answers 200 with
    content-type application/pdf, and delivers only the first few hundred bytes. Measured on
    SATLOA2016062200058 (filed 2016), all 28 of its "Schedule S DOC N.pdf" attachments come back as
    exactly 455 bytes, and the truncation is server-side -- identity encoding, gzip, curl and
    curl_cffi all get the same 455 bytes, while the file's own linearization header declares a full
    length of 87,684.

    Worth distinguishing from a parse failure, because the two mean different things and deserve
    different responses. A truncated transfer is the FCC's data being unavailable, which is a
    coverage fact to publish. A parse failure on complete bytes would be our problem to fix.
    """
    return data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:]


def page_texts(data: bytes) -> list[str]:
    """Per-physical-page text. Index 0 is page 1; citations are 1-based everywhere else.

    Physical pages, deliberately, not the page labels printed on the page. A Schedule S runs its
    front matter in roman numerals, so a printed "ii" and a physical 2 are different things, and a
    citation a reader cannot resolve by scrolling to the Nth page is a citation that will be
    disputed. The printed label can be added later as a second field; it must not replace this one.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return [(page.extract_text() or "") for page in reader.pages]
