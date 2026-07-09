"""Unit tests for the pure normalization primitives (no DB)."""

import datetime as dt

import pytest

from identity.normalize import (
    canonical_object_type,
    norm_cospar,
    norm_name,
    orbital_regime,
    parse_date_loose,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("STARLINK-30042", "starlink 30042"),
        ("Starlink 30042", "starlink 30042"),
        ("STARLINK 30042 (v2)", "starlink 30042"),
        ("STARLINK30042", "starlink 30042"),
        ("Starlink-30042 [+]", "starlink 30042"),
        ("  starlink  30042  ", "starlink 30042"),
        ("ONEWEB-0012", "oneweb 0012"),
        ("", ""),
        (None, ""),
    ],
)
def test_norm_name_starlink_patterns(raw, expected):
    assert norm_name(raw) == expected


@pytest.mark.parametrize(
    "raw,expected,standard",
    [
        ("2023-054A", "2023-054A", True),
        ("2023-54A", "2023-054A", True),
        ("2023 054 A", "2023-054A", True),
        ("1998-067A", "1998-067A", True),
        ("1998-067AB", "1998-067AB", True),
        ("1961 Alpha 1", "1961 ALPHA 1", False),  # pre-1963: passthrough, flagged non-standard
        ("", None, False),
        (None, None, False),
    ],
)
def test_norm_cospar_forms(raw, expected, standard):
    assert norm_cospar(raw) == (expected, standard)


@pytest.mark.parametrize(
    "perigee,apogee,regime",
    [
        (500, 550, "LEO"),
        (1999, 1999, "LEO"),
        (2000, 2000, "MEO"),
        (10000, 12000, "MEO"),
        (35786, 35786, "GEO"),
        (35286, 36286, "GEO"),  # GEO band edges (35786 +/- 500)
        (35285, 36286, "MEO"),  # just below the low edge
        (35286, 36287, "MEO"),  # just above the high edge
        (200, 40000, "HEO"),  # low perigee, high apogee
        (None, 550, "UNKNOWN"),
        (500, None, "UNKNOWN"),
    ],
)
def test_orbital_regime_boundaries(perigee, apogee, regime):
    assert orbital_regime(perigee, apogee) == regime


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2023-03-15", dt.date(2023, 3, 15)),
        ("2023 Mar 15", dt.date(2023, 3, 15)),
        ("2023 Mar", dt.date(2023, 3, 1)),
        ("3/15/2023", dt.date(2023, 3, 15)),
        ("2023", dt.date(2023, 1, 1)),
        (dt.date(2020, 1, 2), dt.date(2020, 1, 2)),
        ("-", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date_loose(raw, expected):
    assert parse_date_loose(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Real GCAT LDate/DDate vague forms straight from .superpowers/sdd/gcat-satcat-head.txt:
        # a full date carries a HHMM time and a trailing '?' uncertainty marker; the day is what we
        # keep. These already worked via .search(), now pinned by tests.
        ("1957 Dec  1 1000?", dt.date(1957, 12, 1)),
        ("1957 Oct  4", dt.date(1957, 10, 4)),
        ("1958 Jan  4?", dt.date(1958, 1, 4)),
        ("1958 Apr 14 0200?", dt.date(1958, 4, 14)),
        ("1970 Mar 31 1045?", dt.date(1970, 3, 31)),
        # Bare year with GCAT's '?' marker: previously returned None (bug the finding flagged);
        # now degrades to the certain year rather than silently dropping the claim entirely.
        ("1971?", dt.date(1971, 1, 1)),
        ("2026?", dt.date(2026, 1, 1)),
        # Genuinely-ambiguous decade forms stay None (which year in the 2000s?).
        ("2000s?", None),
        ("1990s", None),
        # '-' sentinel and blanks stay None.
        ("-", None),
        ("   ", None),
    ],
)
def test_parse_date_loose_gcat_vague_forms(raw, expected):
    assert parse_date_loose(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Plain enum words / single letters.
        ("PAYLOAD", "PAYLOAD"),
        ("PAY", "PAYLOAD"),
        ("P", "PAYLOAD"),
        ("ROCKET BODY", "ROCKET_BODY"),
        ("R/B", "ROCKET_BODY"),
        ("DEBRIS", "DEBRIS"),
        # SATCAT codes (celestrak object_type vocabulary).
        ("DEB", "DEBRIS"),
        ("UNK", "UNKNOWN"),
        # GCAT space-padded SatType strings — the bug: leading class byte must win after strip.
        ("P           ", "PAYLOAD"),   # bare payload, right-padded
        ("P      O    ", "PAYLOAD"),   # payload in orbit
        ("PX-C---", "PAYLOAD"),        # PX non-standard payload
        ("R2", "ROCKET_BODY"),         # 2nd stage
        ("R3", "ROCKET_BODY"),
        ("D  P", "DEBRIS"),            # fragmentation debris piece
        ("C  F", "DEBRIS"),            # component: fairing
        ("C  M", "DEBRIS"),            # component: module/cabin part
        ("S", "PAYLOAD"),              # suborbital payload
        ("Z  X", "UNKNOWN"),           # spurious tracking artifact
        ("X", "UNKNOWN"),              # deleted catalog entry
        # Junk / empty -> UNKNOWN.
        ("TBA", "UNKNOWN"),
        ("", "UNKNOWN"),
        ("   ", "UNKNOWN"),
        (None, "UNKNOWN"),
    ],
)
def test_canonical_object_type(raw, expected):
    assert canonical_object_type(raw) == expected
