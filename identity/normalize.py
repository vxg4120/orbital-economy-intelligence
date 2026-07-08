"""Pure normalization primitives for the identity graph.

No DB, no I/O — table-driven, deterministic, unit-tested in isolation.
"""

from __future__ import annotations

import datetime as dt
import re

# --- name normalization ------------------------------------------------------

_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")  # (v2), [+], {..}
_NONWORD = re.compile(r"[^\w\s]+", re.UNICODE)          # punctuation -> space
_ALPHA_DIGIT = re.compile(r"(?<=[^\W\d_])(?=\d)")        # starlink30042 -> starlink 30042
_DIGIT_ALPHA = re.compile(r"(?<=\d)(?=[^\W\d_])")        # 30042v -> 30042 v
_WS = re.compile(r"\s+")


def norm_name(s: str | None) -> str:
    """Canonicalize a satellite name for fuzzy matching.

    casefold -> drop bracketed suffixes -> punctuation to spaces -> split glued
    alpha/digit runs (unifies STARLINK-30042 / Starlink 30042 / STARLINK30042) ->
    collapse whitespace. Returns '' for empty/None input.
    """
    if not s:
        return ""
    s = str(s).casefold()
    s = _BRACKETED.sub(" ", s)
    s = _NONWORD.sub(" ", s)
    s = _ALPHA_DIGIT.sub(" ", s)
    s = _DIGIT_ALPHA.sub(" ", s)
    return _WS.sub(" ", s).strip()


# --- COSPAR / international designator ----------------------------------------

# YYYY-NNNP(P)(P): 4-digit year, 1-3 digit launch number, 1-3 letter piece.
_COSPAR = re.compile(r"^\s*(\d{4})[-\s]?(\d{1,3})\s*([A-Za-z]{1,3})\s*$")


def norm_cospar(s: str | None) -> tuple[str | None, bool]:
    """Normalize a COSPAR/international designator to canonical ``YYYY-NNNP``.

    Returns ``(value, is_standard)``. SATCAT ``OBJECT_ID`` ('2023-054A') and GCAT
    piece strings both collapse to the same canonical string. Values that do not
    match the modern grammar (pre-1963 Greek-letter designators, junk) pass
    through upper-cased with ``is_standard=False`` so callers can still key on
    them without pretending they are clean.
    """
    if not s:
        return (None, False)
    raw = str(s).strip()
    if not raw:
        return (None, False)
    m = _COSPAR.match(raw)
    if not m:
        return (raw.upper(), False)
    year, num, piece = m.groups()
    return (f"{year}-{int(num):03d}{piece.upper()}", True)


# --- orbital regime -----------------------------------------------------------

_GEO_LOW = 35786 - 500
_GEO_HIGH = 35786 + 500


def orbital_regime(perigee_km: float | None, apogee_km: float | None) -> str:
    """Classify an orbit as LEO | MEO | GEO | HEO | UNKNOWN from its altitudes.

    LEO: apogee < 2000 km. HEO: perigee < 2000 <= apogee (elliptical). GEO: both
    perigee and apogee inside 35786 +/- 500 km (near-circular geostationary band).
    MEO: everything else in between. UNKNOWN if either altitude is missing.
    """
    if perigee_km is None or apogee_km is None:
        return "UNKNOWN"
    try:
        p = float(perigee_km)
        a = float(apogee_km)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if a < 2000:
        return "LEO"
    if p < 2000:  # and a >= 2000
        return "HEO"
    if _GEO_LOW <= p <= _GEO_HIGH and _GEO_LOW <= a <= _GEO_HIGH:
        return "GEO"
    return "MEO"


# --- object type --------------------------------------------------------------


def canonical_object_type(s: str | None) -> str:
    """Map source object-type strings to PAYLOAD | ROCKET_BODY | DEBRIS | UNKNOWN."""
    if not s:
        return "UNKNOWN"
    t = str(s).strip().upper()
    if t.startswith("PAY") or t == "P":
        return "PAYLOAD"
    if "R/B" in t or t.startswith("ROCKET") or t == "R":
        return "ROCKET_BODY"
    if t.startswith("DEB") or t == "D":
        return "DEBRIS"
    return "UNKNOWN"


# --- lenient date parsing (GCAT vague dates, UCS text, SATCAT DATE objects) ----

_MONTHS = {
    m.casefold(): i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )
}
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_TEXT_YMD = re.compile(r"(\d{4})\s+([A-Za-z]{3})\s+(\d{1,2})")
_TEXT_YM = re.compile(r"(\d{4})\s+([A-Za-z]{3})")
_MDY = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_YEAR_ONLY = re.compile(r"^\s*(\d{4})\s*$")


def parse_date_loose(s) -> dt.date | None:
    """Best-effort parse of a date from SATCAT/GCAT/UCS forms; None if not parseable.

    Accepts ``datetime.date`` (SATCAT DATE columns) as-is, ISO 'YYYY-MM-DD',
    GCAT text 'YYYY Mon DD' / 'YYYY Mon' (day/month default to 1), US 'M/D/YYYY',
    and bare 'YYYY'. GCAT '-' sentinels and blanks return None.
    """
    if s is None:
        return None
    if isinstance(s, dt.datetime):
        return s.date()
    if isinstance(s, dt.date):
        return s
    t = str(s).strip()
    if not t or t == "-":
        return None
    m = _ISO.search(t)
    if m:
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    m = _TEXT_YMD.search(t)
    if m and m[2].casefold() in _MONTHS:
        return _safe_date(int(m[1]), _MONTHS[m[2].casefold()], int(m[3]))
    m = _TEXT_YM.search(t)
    if m and m[2].casefold() in _MONTHS:
        return _safe_date(int(m[1]), _MONTHS[m[2].casefold()], 1)
    m = _MDY.search(t)
    if m:
        return _safe_date(int(m[3]), int(m[1]), int(m[2]))
    m = _YEAR_ONLY.match(t)
    if m:
        return _safe_date(int(m[1]), 1, 1)
    return None


def _safe_date(y: int, mo: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None
