"""The build summary's GCAT coverage line.

From 2026-07-21 to 2026-11-14 every GCAT pull landed 69,999 rows because GCAT had moved NORAD
100000 and above into a second file, and the 1,309 satellites catalogued since had no GCAT entry.
Every run reported ok. The coverage line is the downstream signal that was missing, and above 1%
of the graph it starts with "!!", which the nightly forwards to ALERT_URL.
"""

import datetime as dt

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
    """Seeded inside db_conn's transaction, which is never committed. The middle GCAT run is the
    newest OK one, which is what the claims pass reads; a satellite whose current GCAT link names
    a key that run lacks (an empty part 3 on a later night) has no GCAT data and must count as
    without. As in production, the dropped key is still in an older retained OK run, and a newer
    run that has not finished (status NULL) holds it too; neither may count. The reserved NORAD
    range is the top of the catalog, so the seeded gaps are the newest."""

    def run(cur, status, keys):
        cur.execute(
            "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
            "VALUES ('gcat', 'gcat_satcat_coverage_test', now(), now(), %s) "
            "RETURNING ingest_run_id",
            (status,),
        )
        run_id = cur.fetchone()[0]
        cur.executemany(
            "INSERT INTO raw_gcat_satcat (jcat, ingest_run_id) VALUES (%s, %s)",
            [(key, run_id) for key in keys],
        )

    with db_conn.cursor() as cur:
        run(cur, "ok", ["S909999992"])  # an older retained snapshot that still had the key
        run(cur, "ok", ["S909999990", "S909999991", "S909999993", "S909999995"])  # the newest OK
        run(cur, None, ["S909999992"])  # in flight: not a snapshot yet
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


# --- the weekly 60-day reentry predictions (docs/specs/spacetrack-reentry-predictions.md) ---

_NOW = dt.datetime(2026, 11, 26, 7, 30, tzinfo=dt.UTC)


def _reentry_line(capsys, pulled_at, rows, failed=0, objects=None):
    build_graph._print_summary(_summary(reentry_predictions={
        "pulled_at": pulled_at, "rows": rows, "failed_since": failed, "now": _NOW,
        "objects": rows if objects is None else objects}))
    [line] = [x for x in capsys.readouterr().out.splitlines() if "reentry predictions:" in x]
    return line


def test_reentry_predictions_line_names_the_count_and_the_pull_date(capsys):
    line = _reentry_line(capsys, dt.datetime(2026, 11, 25, 19, 10, tzinfo=dt.UTC), 412)
    assert line == "reentry predictions:     412 objects (as of 2026-11-25)"


def test_reentry_predictions_count_objects_not_messages(capsys):
    """A pull can carry two messages for one object (two weekly bulletins in its 8 days)."""
    line = _reentry_line(capsys, dt.datetime(2026, 11, 25, 19, 10, tzinfo=dt.UTC), 3, objects=2)
    assert line == "reentry predictions:     2 objects (as of 2026-11-25)"


def test_reentry_predictions_alert_on_an_empty_pull(capsys):
    line = _reentry_line(capsys, dt.datetime(2026, 11, 25, 19, 10, tzinfo=dt.UTC), 0)
    assert line.startswith("!! reentry predictions:")
    assert line.endswith("the newest pull landed no prediction")


@pytest.mark.parametrize("age_days, alerts", [(9, False), (9.01, True)])
def test_reentry_predictions_alert_when_the_newest_pull_is_over_9_days_old(capsys, age_days,
                                                                           alerts):
    line = _reentry_line(capsys, _NOW - dt.timedelta(days=age_days), 400, failed=2)
    assert line.startswith("!!") is alerts
    assert "; 2 pull(s) failed since" in line


def test_reentry_predictions_before_the_first_pull(capsys):
    assert not _reentry_line(capsys, None, None).startswith("!!")
    assert _reentry_line(capsys, None, None, failed=1) == (
        "!! reentry predictions:     none landed; 1 pull(s) failed")


def test_a_summary_without_predictions_prints_no_reentry_line(capsys):
    build_graph._print_summary(_summary())
    assert "reentry predictions" not in capsys.readouterr().out


def test_reentry_predictions_alert_when_no_date_can_be_read(capsys):
    """The DECAY_EPOCH format is only known from the first live pull; if the strict parse reads
    none of it, the panel is empty, and the summary says why."""
    build_graph._print_summary(_summary(reentry_predictions={
        "pulled_at": dt.datetime(2026, 11, 25, 19, 10, tzinfo=dt.UTC), "rows": 400,
        "objects": 380, "dated": 0, "failed_since": 0, "now": _NOW}))
    [line] = [x for x in capsys.readouterr().out.splitlines() if "reentry predictions:" in x]
    assert line.startswith("!! reentry predictions:")
    assert line.endswith("no prediction has a readable date")


def test_a_failed_predictions_read_alerts_without_failing_the_build(capsys):
    build_graph._print_summary(_summary(reentry_predictions={"error": "UndefinedTable"}))
    [line] = [x for x in capsys.readouterr().out.splitlines() if "reentry predictions:" in x]
    assert line == "!! reentry predictions:     unavailable (UndefinedTable)"


@pytest.mark.db
def test_the_predictions_read_runs_in_a_savepoint(db_conn):
    """A failing query inside it leaves the build's transaction usable."""
    with db_conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE build_probe (x int)")
        cur.execute("INSERT INTO build_probe VALUES (1)")
        cur.execute("CREATE TEMP VIEW v_reentry_prediction_latest AS "
                    "SELECT 1 / 0 AS predicted_date")
        assert build_graph._reentry_predictions(cur) == {"error": "DivisionByZero"}
        cur.execute("SELECT x FROM build_probe")
        assert cur.fetchall() == [(1,)]
    db_conn.rollback()

