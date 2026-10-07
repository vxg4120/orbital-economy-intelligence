"""Claims, not copies (identity/claims.py, migration 0023, docs/specs/assertion-model.md).

Each test seeds its own runs and rows and rolls back.
"""

import datetime as dt

import pytest

from identity import assertions, claims

pytestmark = pytest.mark.db

T0 = dt.datetime(2026, 10, 1, 7, 10, tzinfo=dt.UTC)


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


def test_a_run_opens_closes_and_leaves_unchanged_claims_alone(db_conn):
    try:
        with db_conn.cursor() as cur:
            r1, r2 = _run(cur), _run(cur)
        day = dt.timedelta(days=1)
        assert claims.record(db_conn, "satcat", r1, T0, [
            ("1", "owner", "NASA"), ("1", "status", "+"), ("2", "owner", "ESA"),
        ]) == (0, 3)
        # Run 2: 1's owner changes, 1's status is unchanged, 2 vanishes, 3 appears.
        assert claims.record(db_conn, "satcat", r2, T0 + day, [
            ("1", "owner", "NASA/GSFC"), ("1", "status", "+"), ("3", "owner", "JAXA"),
        ]) == (2, 2)
        with db_conn.cursor() as cur:
            assert _open(cur) == [("1", "owner", "NASA/GSFC", r2), ("1", "status", "+", r1),
                                  ("3", "owner", "JAXA", r2)]
            assert _all(cur) == [
                ("1", "owner", "NASA", r1, r2), ("1", "status", "+", r1, None),
                ("2", "owner", "ESA", r1, r2), ("1", "owner", "NASA/GSFC", r2, None),
                ("3", "owner", "JAXA", r2, None),
            ]  # ordered by (first_run, key, attribute, value)
            cur.execute("SELECT observed_to FROM claim WHERE source_key = '2'")
            assert cur.fetchone()[0] == T0 + day
    finally:
        db_conn.rollback()


def test_recording_the_same_run_twice_writes_nothing(db_conn):
    try:
        with db_conn.cursor() as cur:
            r1 = _run(cur)
        rows = [("1", "owner", "NASA"), ("1", "status", "+")]
        assert claims.record(db_conn, "satcat", r1, T0, rows) == (0, 2)
        assert claims.record(db_conn, "satcat", r1, T0, rows) == (0, 0)
        with db_conn.cursor() as cur:
            assert len(_all(cur)) == 2
    finally:
        db_conn.rollback()


def test_a_run_older_than_the_last_recorded_is_refused(db_conn):
    """Recording out of order would close everything the newer run makes."""
    try:
        with db_conn.cursor() as cur:
            r1, r2 = _run(cur), _run(cur)
        claims.record(db_conn, "satcat", r2, T0, [("1", "owner", "NASA")])
        assert claims.record(db_conn, "satcat", r1, T0, [("9", "owner", "X")]) == (0, 0)
        with db_conn.cursor() as cur:
            assert _open(cur) == [("1", "owner", "NASA", r2)]
    finally:
        db_conn.rollback()


def test_a_value_that_flips_back_is_three_rows(db_conn):
    try:
        with db_conn.cursor() as cur:
            runs = [_run(cur) for _ in range(3)]
        for run, value in zip(runs, ("A", "B", "A")):
            claims.record(db_conn, "satcat", run, T0, [("1", "status", value)])
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
            run = _run(cur, "gcat")
            cur.execute(
                "INSERT INTO raw_gcat_satcat (jcat, norad_id, piece, name, pl_name, owner, "
                "status, bus, manufacturer, decay_date, object_type, ingest_run_id) VALUES "
                "('S1', 1, '2026-001A', 'ONE', 'ONE PL', 'NASA', 'O', '-', 'ACME', NULL, 'P', %(r)s), "
                "('S2', 2, '2026-001B', 'TWO', NULL, NULL, 'D', 'X-BUS', '', '2026-01-02', 'P', %(r)s)",
                {"r": run},
            )
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
        claims.record(db_conn, "gcat", run, T0, [("S970002002", "owner", "OWN"),
                                                ("S970002003", "owner", "NOBODY")])
        with db_conn.cursor() as cur:
            cur.execute("SELECT satellite_id, value FROM v_current_claim WHERE source = 'gcat' "
                        "AND source_key LIKE 'S97000200%'")
            assert cur.fetchall() == [(b, "OWN")]  # a's link is retired; S970002003 has none
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
                        (key, attribute, value, T0 + dt.timedelta(days=i), run),
                    )
            cur.execute("DELETE FROM claim WHERE source = 'satcat'")
        todo = [(s, r) for s, r in build_claims.runs_to_replay(db_conn) if r in runs]
        assert [r for _, r in todo] == runs
        results = [build_claims.replay(db_conn, s, r) for s, r in todo]
        assert results == [(0, 2), (1, 1), (1, 0)]
        with db_conn.cursor() as cur:
            assert _open(cur) == [("1", "owner", "NASA/GSFC", runs[1])]
            assert len(_all(cur)) == 3
        # Resumable: nothing left to replay, and replaying the last run again writes nothing.
        assert [(s, r) for s, r in build_claims.runs_to_replay(db_conn) if r in runs] == [
            ("satcat", runs[2])
        ]
        assert build_claims.replay(db_conn, "satcat", runs[2]) == (0, 0)
    finally:
        db_conn.rollback()
