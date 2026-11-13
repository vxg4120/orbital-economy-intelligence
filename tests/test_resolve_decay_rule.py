"""The decay-message outvote rule without a database (identity/resolve.py).

A Space-Track decay message outranks SATCAT and GCAT, except when the two catalogs agree to
within CATALOGS_AGREE_DAYS (1) and the message is more than MESSAGE_OUTLIER_DAYS (30) from
SATCAT. The db test in tests/test_identity_resolve.py runs the rule through the resolver; these
pin its boundaries, its guards and the build-summary line under `pytest -m "not db"` as well.
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_graph  # noqa: E402

from identity import resolve  # noqa: E402
from identity.normalize import has_day, parse_date_loose  # noqa: E402


def _sources(message, satcat, gcat):
    """The resolver's by_source shape, {source: (value, observed_to)}, leaving out a None."""
    values = {"spacetrack_decay": message, "satcat": satcat, "gcat": gcat}
    return {src: (value, None) for src, value in values.items() if value is not None}


def _outvoted(message, satcat, gcat):
    return resolve._contradicted_by_catalogs(_sources(message, satcat, gcat))


def test_the_thresholds_are_the_measured_ones():
    assert resolve.CATALOGS_AGREE_DAYS == 1
    assert resolve.MESSAGE_OUTLIER_DAYS == 30


@pytest.mark.parametrize(
    ("satcat", "gcat", "outvoted"),
    [
        ("2025-01-07", "2025 Jan  7", True),    # catalogs agree exactly
        ("2025-01-07", "2025 Jan  8", True),    # 1 day apart still agrees
        ("2025-01-07", "2025 Jan  6", True),    # either side of SATCAT
        ("2025-01-07", "2025 Jan  9", False),   # 2 days apart do not agree
        ("2025-01-07", "2025 Jan  5", False),
    ],
)
def test_catalogs_agree_to_within_one_day(satcat, gcat, outvoted):
    assert _outvoted("2024-01-07", satcat, gcat) is outvoted


@pytest.mark.parametrize(
    ("message", "outvoted"),
    [
        ("2024-03-31", False),  # 30 days before SATCAT keeps its precedence
        ("2024-03-30", True),   # 31 days before is outvoted
        ("2024-05-30", False),  # 30 days after
        ("2024-05-31", True),   # 31 days after
    ],
)
def test_a_message_more_than_thirty_days_from_satcat_is_outvoted(message, outvoted):
    assert _outvoted(message, "2024-04-30", "2024 Apr 30") is outvoted


def test_norad_48587_is_outvoted():
    """Production on 2026-11-12 and 2026-11-13: the message says 2024-01-07, SATCAT and GCAT
    say 2025-01-07, the same day a year early."""
    assert _outvoted("2024-01-07", "2025-01-07", "2025 Jan  7 0055") is True


def test_an_uncertain_gcat_date_that_gives_a_day_still_votes():
    assert _outvoted("2024-01-07", "2025-01-07", "2025 Jan  7?") is True


@pytest.mark.parametrize(
    ("message", "satcat", "gcat"),
    [
        (None, "2025-01-07", "2025 Jan  7"),
        ("2024-01-07", None, "2025 Jan  7"),
        ("2024-01-07", "2025-01-07", None),
    ],
)
def test_the_rule_needs_all_three_dates(message, satcat, gcat):
    assert _outvoted(message, satcat, gcat) is False


@pytest.mark.parametrize("gcat", ["2000s?", "2010 Q3?", "-", ""])
def test_an_unparseable_gcat_date_does_not_vote(gcat):
    assert _outvoted("2024-01-07", "2025-01-07", gcat) is False


@pytest.mark.parametrize(
    ("message", "satcat", "gcat"),
    [
        # GCAT's year parses to 2007-01-01 and agrees with SATCAT's 2006-12-31, but the message
        # falls inside GCAT's year, so GCAT does not contradict it.
        ("2007-03-01", "2006-12-31", "2007?"),
        ("2007-03-01", "2006-12-31", "2007"),
        # The message falls inside GCAT's month, which parses to its 1st.
        ("2024-05-31", "2024-04-30", "2024 May"),
        # Outside GCAT's month, but a month still does not vote: the rule stays narrow.
        ("2024-06-15", "2024-05-01", "2024 May?"),
    ],
)
def test_a_gcat_date_without_a_day_does_not_vote(message, satcat, gcat):
    # The same values with the old rule (parsed dates only) would have been outvoted.
    sc, gc, st = parse_date_loose(satcat), parse_date_loose(gcat), parse_date_loose(message)
    assert abs((sc - gc).days) <= 1 and abs((st - sc).days) > 30
    assert _outvoted(message, satcat, gcat) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (dt.date(2025, 1, 7), True),
        (dt.datetime(2025, 1, 7, 0, 55), True),
        ("2025-01-07", True),
        ("2025 Jan  7 0055", True),
        ("1957 Dec  1 1000?", True),
        ("2024 Mar 29?", True),
        ("1/7/2025", True),
        ("2024 May?", False),
        ("1983 Jan", False),
        ("2007?", False),
        ("2007", False),
        ("2000s?", False),
        ("2010 Q3?", False),
        ("2024 Feb 30", False),  # not a real day, so it does not parse
        ("2024 Xyz 7", False),
        ("-", False),
        ("", False),
        (None, False),
    ],
)
def test_has_day(raw, expected):
    """has_day is True only for a value that parse_date_loose also parses."""
    assert has_day(raw) is expected
    if expected:
        assert parse_date_loose(raw) is not None


def _summary(**overrides):
    s = {
        "satellites": 3, "merge_log_rows": 0, "auto_links": 0, "ambiguous_cospar_links": 0,
        "review_queue_size": 0, "identifiers_by_type": {}, "assertions_by_source": {},
        "operator_coverage_pct": 0.0, "operator_resolved": 0, "status_coverage_pct": 0.0,
        "status_resolved": 0, "unmapped_status": [], "unmatched_owners": [],
    }
    s.update(overrides)
    return s


def test_the_build_summary_prints_the_outvoted_norad_ids(capsys):
    build_graph._print_summary(_summary(decay_messages_outvoted=[48587],
                                        display_name_overrides=7))
    out = capsys.readouterr().out.splitlines()
    assert "decay messages outvoted: 1  (NORAD 48587)" in out
    assert "display name overrides:  7" in out


def test_the_build_summary_prints_zero_outvoted(capsys):
    """A count of 0 is printed rather than left out, since a drop to 0 is news too."""
    build_graph._print_summary(_summary(decay_messages_outvoted=[]))
    out = capsys.readouterr().out.splitlines()
    assert "decay messages outvoted: 0" in out


def test_the_outvoted_line_caps_the_ids_it_lists():
    line = build_graph._outvoted_line(list(range(1, 26)))
    assert line.startswith("decay messages outvoted: 25  (NORAD 1, 2, 3,")
    assert line.endswith("19, 20 and 5 more)")
