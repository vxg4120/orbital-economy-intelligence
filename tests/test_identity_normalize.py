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
        ("PAYLOAD", "PAYLOAD"),
        ("PAY", "PAYLOAD"),
        ("P", "PAYLOAD"),
        ("ROCKET BODY", "ROCKET_BODY"),
        ("R/B", "ROCKET_BODY"),
        ("DEBRIS", "DEBRIS"),
        ("TBA", "UNKNOWN"),
        (None, "UNKNOWN"),
    ],
)
def test_canonical_object_type(raw, expected):
    assert canonical_object_type(raw) == expected
