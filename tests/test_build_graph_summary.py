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


def _seed_satellite(cur, norad, gcat_keys=(), expired_keys=()):
    cur.execute(
        "INSERT INTO satellite (norad_id, canonical_name) VALUES (%s, 'ZZ GCAT COVERAGE') "
        "RETURNING satellite_id",
        (norad,),
    )
    sat_id = cur.fetchone()[0]
    for key, valid_to in [(k, None) for k in gcat_keys] + [(k, "2026-01-01") for k in expired_keys]:
        cur.execute(
            "INSERT INTO satellite_identifier (satellite_id, id_type, id_value, source, valid_to) "
            "VALUES (%s, 'gcat_id', %s, 'gcat', %s)",
            (sat_id, key, valid_to),
        )


@pytest.mark.db
def test_coverage_counts_satellites_whose_gcat_key_is_in_the_newest_snapshot(db_conn):
    """Seeded inside db_conn's transaction, which is never committed. The seeded GCAT run is the
    newest OK one, which is what the claims pass reads; a satellite whose current GCAT link names
    a key that run lacks (an empty part 3 on a later night) has no GCAT data and must count as
    without. The reserved NORAD range is the top of the catalog, so the seeded gaps are newest."""
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
            "VALUES ('gcat', 'gcat_satcat_coverage_test', now(), now(), 'ok') "
            "RETURNING ingest_run_id"
        )
        run = cur.fetchone()[0]
        in_run = ["S909999990", "S909999991", "S909999993", "S909999995"]
        cur.executemany(
            "INSERT INTO raw_gcat_satcat (jcat, ingest_run_id) VALUES (%s, %s)",
            [(key, run) for key in in_run],
        )
        before = build_graph._gcat_coverage(cur)

        _seed_satellite(cur, 909999991, gcat_keys=["S909999991", "S909999990"])  # two keys: once
        _seed_satellite(cur, 909999995, gcat_keys=["S909999995"])
        _seed_satellite(cur, 909999992, gcat_keys=["S909999992"])  # key not in the newest run
        _seed_satellite(cur, 909999993, expired_keys=["S909999993"])  # link retired
        _seed_satellite(cur, 909999994)  # never linked
        _seed_satellite(cur, None)  # no NORAD to name in the gap list
        after = build_graph._gcat_coverage(cur)

    assert after["with"] - before["with"] == 2
    assert after["without"] - before["without"] == 4
    assert after["newest_without"][:3] == [909999994, 909999993, 909999992]
    assert None not in after["newest_without"]
    assert len(after["newest_without"]) <= 5
