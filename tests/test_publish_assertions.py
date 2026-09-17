"""quality/assertions.py: the pre-publish invariants, on synthetic rows and on the live views.

The network-free tests feed each check one good row and one row breaking exactly its rule, so
a check that goes vacuous (passes everything) or greedy (fails good rows) is caught without a
database. The db-marked test runs the whole suite the nightly runs, against the dev database.
"""

import pytest

from quality import assertions as a

GOOD = {
    "slug": "spx",
    "fleet_total": 100,
    "fleet_on_orbit": 90,
    "fleet_active": 80,
    "decayed_count": 10,
    "decayed_share_pct": 10.0,
    "lifetime_n": 8,
    "tto_n": 40,
    "sk_n": 70,
    "station_keeping_share_pct": 95.5,
    "disposal_n": 0,
    "disposal_compliance_pct": None,
    "gp_n": 85,
    "gp_coverage_pct": 85.0,
}


def _row(**over):
    return {**GOOD, **over}


def test_a_consistent_row_passes_every_check():
    assert a.check_leaderboard([GOOD], "view", "slug") == []


def test_on_orbit_above_fleet_is_caught():
    # The Glonass regression from the audit's morning pass: ON-ORBIT 507 against FLEET 505.
    bad = _row(fleet_total=505, fleet_on_orbit=507, fleet_active=400, decayed_count=-2)
    found = a.check_fleet_order([bad], "v", "slug")
    assert len(found) == 1 and "fleet 505 >= on-orbit 507" in found[0]


def test_active_above_on_orbit_and_negative_counts_are_caught():
    assert a.check_fleet_order([_row(fleet_active=95)], "v", "slug")
    assert a.check_fleet_order([_row(fleet_active=-1, fleet_on_orbit=0)], "v", "slug")


def test_status_partition_must_hold():
    assert a.check_status_partition([GOOD], "v", "slug") == []
    assert a.check_status_partition([_row(decayed_count=11)], "v", "slug")
    # A surface without a decayed column (the operator league) is not judged on it.
    row = {k: v for k, v in GOOD.items() if k != "decayed_count"}
    assert a.check_status_partition([row], "v", "slug") == []


def test_metric_counts_never_exceed_the_fleet():
    assert a.check_metric_counts([_row(sk_n=101)], "v", "slug")
    assert a.check_metric_counts([_row(gp_n=-1)], "v", "slug")
    assert a.check_metric_counts([_row(disposal_n=None)], "v", "slug") == []


def test_percentages_stay_within_0_100_or_null():
    assert a.check_percentages([_row(station_keeping_share_pct=120.0)], "v", "slug")
    assert a.check_percentages([_row(gp_coverage_pct=-0.1)], "v", "slug")
    assert a.check_percentages([_row(disposal_compliance_pct=None)], "v", "slug") == []
    assert a.check_percentages([_row(decayed_share_pct=100)], "v", "slug") == []


def test_public_keys_are_present_and_unique():
    assert a.check_unique_keys([GOOD, _row(slug="boe")], "v", "slug") == []
    dup = a.check_unique_keys([GOOD, GOOD], "v", "slug")
    assert len(dup) == 1 and "appears 2 times" in dup[0]
    assert a.check_unique_keys([_row(slug=None)], "v", "slug")


def test_cohort_floor_is_respected():
    assert a.check_cohort_floor([GOOD], "board", "slug", 5) == []
    assert a.check_cohort_floor([_row(fleet_total=4)], "board", "slug", 5)


def test_header_counters_reconcile_with_their_tables():
    stats = {
        "satellites": 70_650, "operators": 1_443, "gp_elements": 11_000_000,
        "on_orbit_payloads": 12_000,
        "coverage": {"operator_pct": 100.0, "status_pct": 76.8, "multi_source_pct": 100.0},
        "conflicts": {"status": 34, "decay": 4_273, "stale_owners": 170},
    }
    assert a.check_header_counters(stats, 1_443) == []
    # Audit major 4: the header said 1,443 while the league said 1,390.
    found = a.check_header_counters(stats, 1_390)
    assert len(found) == 1 and "1443 != league total 1390" in found[0]
    broken = {**stats, "conflicts": {**stats["conflicts"], "decay": None}}
    assert a.check_header_counters(broken, 1_443)
    off = {**stats, "coverage": {**stats["coverage"], "status_pct": 101.0}}
    assert a.check_header_counters(off, 1_443)


@pytest.mark.db
def test_live_views_pass_the_publish_assertions(db_conn):
    """What the nightly runs: no served aggregate may break an invariant."""
    violations = a.run(db_conn)
    assert violations == [], "\n".join(violations[:20])
