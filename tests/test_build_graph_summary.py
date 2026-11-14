"""The build summary's GCAT coverage line.

From 2026-07-21 to 2026-11-14 every GCAT pull landed 69,999 rows because GCAT had moved NORAD
100000 and above into a second file, and the 1,309 satellites catalogued since had no GCAT entry.
Every run reported ok. The coverage line is the downstream signal that was missing, and above 1%
of the graph it starts with "!!", which the nightly forwards to ALERT_URL.
"""

import pytest

from scripts import build_graph


def _summary(**overrides):
    s = {
        "satellites": 71308, "merge_log_rows": 0, "auto_links": 0, "ambiguous_cospar_links": 0,
        "review_queue_size": 0, "identifiers_by_type": {}, "assertions_by_source": {},
        "operator_coverage_pct": 100.0, "operator_resolved": 71308, "status_coverage_pct": 0.0,
        "status_resolved": 0, "unmapped_status": [], "unmatched_owners": [],
    }
    s.update(overrides)
    return s


def _coverage_line(capsys, satellites, without, newest):
    build_graph._print_summary(_summary(
        satellites=satellites,
        gcat_coverage={"with": satellites - without, "without": without, "newest_without": newest},
    ))
    [line] = [x for x in capsys.readouterr().out.splitlines() if "gcat coverage:" in x]
    return line


def test_the_summary_prints_gcat_coverage_with_the_newest_gaps(capsys):
    line = _coverage_line(capsys, 71308, 2, [101357, 101146])
    assert line == "gcat coverage:           100.0%  (71306; 2 without, newest NORAD 101357, 101146)"


def test_a_complete_graph_prints_no_gap_list(capsys):
    assert _coverage_line(capsys, 10, 0, []) == "gcat coverage:           100.0%  (10; 0 without)"


def test_the_four_month_gap_would_have_alerted(capsys):
    line = _coverage_line(capsys, 71308, 1309, [101357, 101356, 101355, 101354, 101353])
    assert line.startswith("!! gcat coverage:")  # the nightly greps '^!!'
    assert "98.2%  (69999; 1309 without, newest NORAD 101357, 101356, 101355, 101354, 101353)" in line
    assert line.endswith("more than 1% of satellites have no GCAT entry")


@pytest.mark.parametrize("without, alerts", [(713, False), (714, True)])
def test_the_alert_starts_above_one_percent(capsys, without, alerts):
    line = _coverage_line(capsys, 71300, without, [1])
    assert line.startswith("!!") is alerts


def test_a_summary_without_the_key_prints_no_coverage_line(capsys):
    """Older callers (and the tests that build a summary by hand) pass no gcat_coverage."""
    build_graph._print_summary(_summary())
    assert "gcat coverage" not in capsys.readouterr().out


@pytest.mark.db
def test_coverage_counts_only_current_gcat_links(db_conn):
    """Seeded inside db_conn's transaction, which is never committed. The reserved NORAD range
    is the top of the catalog, so the seeded gaps are the newest ones."""
    with db_conn.cursor() as cur:
        ids = {}
        for norad in (909999997, 909999998, 909999999):
            cur.execute(
                "INSERT INTO satellite (norad_id, canonical_name) VALUES (%s, 'ZZ GCAT COVERAGE') "
                "RETURNING satellite_id",
                (norad,),
            )
            ids[norad] = cur.fetchone()[0]
        # 909999999 has a current GCAT id, 909999997 only an expired one, 909999998 none.
        cur.execute(
            "INSERT INTO satellite_identifier (satellite_id, id_type, id_value, source, valid_to) "
            "VALUES (%s, 'gcat_id', 'S909999999', 'gcat', NULL), "
            "(%s, 'gcat_id', 'S909999997', 'gcat', '2026-01-01')",
            (ids[909999999], ids[909999997]),
        )
        coverage = build_graph._gcat_coverage(cur)
        cur.execute("SELECT count(*) FROM satellite")
        (satellites,) = cur.fetchone()

    assert coverage["with"] + coverage["without"] == satellites
    assert coverage["newest_without"][:2] == [909999998, 909999997]
    assert 909999999 not in coverage["newest_without"]
    assert len(coverage["newest_without"]) <= 5
