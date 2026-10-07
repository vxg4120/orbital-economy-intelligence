"""Claims, not copies (identity/claims.py, migration 0023, docs/specs/assertion-model.md).

Each test seeds its own runs and rows and rolls back.
"""

import datetime as dt

import pytest

from identity import assertions, claims

pytestmark = pytest.mark.db

T0 = dt.datetime(2026, 10, 1, 7, 10, tzinfo=dt.UTC)
DAY = dt.timedelta(days=1)


def _run(cur, source="celestrak"):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
        "VALUES (%s, 'test://claims', now(), now(), 'ok') RETURNING ingest_run_id",
        (source,),
    )
    return cur.fetchone()[0]


def _open(cur, source="satcat"):
    cur.execute(
        "SELECT source_key, attribute, value, first_run FROM claim "
        "WHERE source = %s AND closed_run IS NULL ORDER BY 1, 2, 3",
        (source,),
    )
    return cur.fetchall()


def _all(cur, source="satcat"):
    cur.execute(
        "SELECT source_key, attribute, value, first_run, closed_run FROM claim "
        "WHERE source = %s ORDER BY first_run, source_key, attribute, value",
        (source,),
    )
    return cur.fetchall()


def _record(conn, run, at, rows, source="satcat"):
    return claims.record(conn, source, run, at, rows, bootstrap=True)


def test_a_run_opens_closes_and_leaves_unchanged_claims_alone(db_conn):
    try:
        with db_conn.cursor() as cur:
            r1, r2 = _run(cur), _run(cur)
        assert _record(db_conn, r1, T0, [
            ("1", "owner", "NASA"), ("1", "status", "+"), ("2", "owner", "ESA"),
        ]) == (0, 3)
        # Run 2: 1's owner changes, 1's status is unchanged, 2 vanishes, 3 appears.
        assert _record(db_conn, r2, T0 + DAY, [
            ("1", "owner", "NASA/GSFC"), ("1", "status", "+"), ("3", "owner", "JAXA"),
        ]) == (2, 2)
        with db_conn.cursor() as cur:
            assert _open(cur) == [("1", "owner", "NASA/GSFC", r2), ("1", "status", "+", r1),
                                  ("3", "owner", "JAXA", r2)]
            assert _all(cur) == [
                ("1", "owner", "NASA", r1, r2), ("1", "status", "+", r1, None),
                ("2", "owner", "ESA", r1, r2), ("1", "owner", "NASA/GSFC", r2, None),
                ("3", "owner", "JAXA", r2, None),
            ]
            # Closed at run 2, last observed when run 1 was: the observation, not the absence.
            cur.execute("SELECT observed_to FROM claim WHERE source_key = '2'")
            assert cur.fetchone()[0] == T0
            cur.execute("SELECT last_run, observed_at FROM claim_progress WHERE source = 'satcat'")
            assert cur.fetchone() == (r2, T0 + DAY)
    finally:
        db_conn.rollback()


def test_a_run_at_or_before_the_last_recorded_is_refused(db_conn):
    """Twice the same run changes nothing even with different rows, and an older run would
    close everything the newer one makes. An unchanged run still advances the progress row,
    so a late run cannot slip in behind it."""
    try:
        with db_conn.cursor() as cur:
            r1, r2, r3 = _run(cur), _run(cur), _run(cur)
        assert _record(db_conn, r1, T0, [("1", "owner", "NASA")]) == (0, 1)
        assert _record(db_conn, r1, T0, [("1", "owner", "CHANGED")]) == (0, 0)
        assert _record(db_conn, r3, T0 + 2 * DAY, [("1", "owner", "NASA")]) == (0, 0)
        assert _record(db_conn, r2, T0 + DAY, [("1", "owner", "LATE")]) == (0, 0)
        with db_conn.cursor() as cur:
            assert _open(cur) == [("1", "owner", "NASA", r1)]
            cur.execute("SELECT last_run FROM claim_progress WHERE source = 'satcat'")
            assert cur.fetchone()[0] == r3
    finally:
        db_conn.rollback()


def test_an_empty_snapshot_is_not_evidence_that_every_claim_ended(db_conn):
    try:
        with db_conn.cursor() as cur:
            r1, r2 = _run(cur), _run(cur)
        _record(db_conn, r1, T0, [("1", "owner", "NASA")])
        assert _record(db_conn, r2, T0 + DAY, []) == (0, 0)
        with db_conn.cursor() as cur:
            assert _open(cur) == [("1", "owner", "NASA", r1)]
    finally:
        db_conn.rollback()


def test_the_nightly_records_nothing_for_a_feed_whose_history_is_not_replayed(db_conn):
    """The replay creates the progress row; until then a nightly run would be recorded first
    and every older run refused, and the history lost."""
    try:
        with db_conn.cursor() as cur:
            r1 = _run(cur)
        assert claims.record(db_conn, "satcat", r1, T0, [("1", "owner", "NASA")]) == (0, 0)
        # The nightly path, before the fetch: a feed without progress records nothing, and the
        # run already recorded is not fetched again.
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO raw_satcat (norad_cat_id, object_name, ingest_run_id) "
                        "VALUES (1, 'ONE', %s)", (r1,))
        assert claims.record_feed(db_conn, "raw_satcat", "satcat", "norad_cat_id",
                                  assertions._SATCAT_ATTRS) is None
        assert _record(db_conn, r1, T0, [("1", "owner", "NASA")]) == (0, 1)
        assert claims.record_feed(db_conn, "raw_satcat", "satcat", "norad_cat_id",
                                  assertions._SATCAT_ATTRS) == (0, 0)
    finally:
        db_conn.rollback()


def test_a_value_that_flips_back_is_three_rows(db_conn):
    try:
        with db_conn.cursor() as cur:
            runs = [_run(cur) for _ in range(3)]
        for i, (run, value) in enumerate(zip(runs, ("A", "B", "A"))):
            _record(db_conn, run, T0 + i * DAY, [("1", "status", value)])
        with db_conn.cursor() as cur:
            assert [(r[2], r[3], r[4]) for r in _all(cur)] == [
                ("A", runs[0], runs[1]), ("B", runs[1], runs[2]), ("A", runs[2], None),
            ]
    finally:
        db_conn.rollback()


def test_feeds_record_the_same_rows_the_assertion_writer_extracts(db_conn):
    """The two writers read the raw snapshot through the same (attribute, column) lists; on a
    seeded GCAT snapshot their outputs agree row for row, NULLs and '-' placeholders included."""
    try:
        with db_conn.cursor() as cur:
            earlier, run = _run(cur, "gcat"), _run(cur, "gcat")
            cur.execute(
                "INSERT INTO raw_gcat_satcat (jcat, norad_id, piece, name, pl_name, owner, "
                "status, bus, manufacturer, decay_date, object_type, ingest_run_id) VALUES "
                "('S1', 1, '2026-001A', 'ONE', 'ONE PL', 'NASA', 'O', '-', 'ACME', NULL, 'P', %(r)s), "
                "('S2', 2, '2026-001B', 'TWO', NULL, NULL, 'D', 'X-BUS', '', '2026-01-02', 'P', %(r)s)",
                {"r": run},
            )
            cur.execute("INSERT INTO claim_progress VALUES ('gcat', %s, %s)", (earlier, T0))
        assertions._extract(db_conn, "raw_gcat_satcat", "gcat", "jcat", "gcat_id",
                            assertions._GCAT_ATTRS, run)
        assert claims.record_feed(db_conn, "raw_gcat_satcat", "gcat", "jcat",
                                  assertions._GCAT_ATTRS) == (0, 10)
        with db_conn.cursor() as cur:
            cur.execute("SELECT source_key, attribute, value FROM source_assertion "
                        "WHERE source = 'gcat' AND ingest_run_id = %s ORDER BY 1, 2, 3", (run,))
            extracted = cur.fetchall()
            cur.execute("SELECT source_key, attribute, value FROM claim WHERE source = 'gcat' "
                        "AND first_run = %s ORDER BY 1, 2, 3", (run,))
            assert cur.fetchall() == extracted
            assert len(extracted) == 10
    finally:
        db_conn.rollback()


def test_the_current_view_reaches_satellites_through_current_links_only(db_conn):
    try:
        with db_conn.cursor() as cur:
            run = _run(cur, "gcat")
            cur.execute("INSERT INTO satellite (norad_id, canonical_name) VALUES (970002001, 'A') "
                        "RETURNING satellite_id")
            a = cur.fetchone()[0]
            cur.execute("INSERT INTO satellite (norad_id, canonical_name) VALUES (970002002, 'B') "
                        "RETURNING satellite_id")
            b = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO satellite_identifier (satellite_id, id_type, id_value, source, "
                "valid_to) VALUES (%s, 'gcat_id', 'S970002002', 'gcat', NULL), "
                "(%s, 'gcat_id', 'S970002002', 'gcat', '2026-10-01')",
                (b, a),
            )
        _record(db_conn, run, T0, [("S970002002", "owner", "OWN"),
                                   ("S970002003", "owner", "NOBODY")], source="gcat")
        with db_conn.cursor() as cur:
            cur.execute("SELECT satellite_id, value, observed_to FROM v_current_claim "
                        "WHERE source = 'gcat' AND source_key LIKE 'S97000200%'")
            assert cur.fetchall() == [(b, "OWN", T0)]  # a's link is retired; S970002003 has none
    finally:
        db_conn.rollback()


def test_replaying_history_gives_the_same_claims_as_recording_it_live(db_conn):
    """scripts/build_claims.py replays source_assertion run by run through the same writer, so a
    history replayed later equals the claims a nightly would have recorded as it happened."""
    from scripts import build_claims

    try:
        with db_conn.cursor() as cur:
            runs = [_run(cur) for _ in range(3)]
            history = {
                runs[0]: [("1", "owner", "NASA"), ("2", "owner", "ESA")],
                runs[1]: [("1", "owner", "NASA/GSFC"), ("2", "owner", "ESA")],
                runs[2]: [("1", "owner", "NASA/GSFC")],
            }
            for i, (run, rows) in enumerate(history.items()):
                for key, attribute, value in rows:
                    cur.execute(
                        "INSERT INTO source_assertion (satellite_id, source_key, attribute, "
                        "value, source, observed_at, ingest_run_id) VALUES (NULL, %s, %s, %s, "
                        "'satcat', %s, %s)",
                        (key, attribute, value, T0 + i * DAY, run),
                    )
            cur.execute("DELETE FROM claim WHERE source = 'satcat'")
            cur.execute("DELETE FROM claim_progress WHERE source = 'satcat'")
        todo = [r for r in build_claims.runs_to_replay(db_conn, "satcat") if r in runs]
        assert todo == runs
        assert [build_claims.replay(db_conn, "satcat", r) for r in todo] == [(0, 2), (1, 1), (1, 0)]
        with db_conn.cursor() as cur:
            assert _open(cur) == [("1", "owner", "NASA/GSFC", runs[1])]
            assert len(_all(cur)) == 3
            cur.execute("SELECT observed_to FROM claim WHERE source_key = '2'")
            assert cur.fetchone()[0] == T0 + DAY  # last seen in run 2, absent from run 3
        # Resumable: nothing left, and the last run again is refused.
        assert [r for r in build_claims.runs_to_replay(db_conn, "satcat") if r in runs] == []
        assert build_claims.replay(db_conn, "satcat", runs[2]) == (0, 0)
    finally:
        db_conn.rollback()
