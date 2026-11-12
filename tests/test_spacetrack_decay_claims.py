"""Space-Track's actual decays as a claims feed (docs/specs/spacetrack-topup.md, the resolver
wiring criterion; migration 0028).

Each nightly landing carries only the Historical messages whose MSG_EPOCH falls in the last 30
days. identity/claims.py folds every landing into spacetrack_decay_message, which keeps each
message ever seen: a message is withdrawn only when a newer landing's window covers its epoch
and it is missing. The feed is the best surviving message per object, recorded as an ordinary
snapshot, and reaches a satellite through its SATCAT NORAD link (v_claim's second branch).

Each test seeds its own runs and rows and rolls back.
"""

import datetime as dt

import pytest
from psycopg.rows import dict_row

from identity import claims, resolve
from tests.claimfix import seed_claim

pytestmark = pytest.mark.db

SRC = claims.SPACETRACK_DECAY
NORAD_A, NORAD_B, NORAD_UNMATCHED = 980000101, 980000102, 980000199
NOW = dt.datetime.now(dt.UTC).replace(microsecond=0)
RECENT = NOW - dt.timedelta(days=5)  # inside every landing's window
AGED = NOW - dt.timedelta(days=40)  # outside the newest landing's window


@pytest.fixture
def conn(db_conn):
    """The test's transaction, with no folded Space-Track history visible: rows a populated dev
    database already holds would otherwise join the snapshot."""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM spacetrack_decay_message")
        cur.execute("DELETE FROM claim WHERE source = %s", (SRC,))
        cur.execute("DELETE FROM claim_progress WHERE source = %s", (SRC,))
        cur.execute("DELETE FROM raw_spacetrack_decay")
    try:
        yield db_conn
    finally:
        db_conn.row_factory = None
        db_conn.rollback()


def _run(cur, started=NOW, status="ok"):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
        "VALUES ('spacetrack', 'decay', %s, %s, %s) RETURNING ingest_run_id",
        (started, started, status),
    )
    return cur.fetchone()[0]


def _decay(cur, run, norad, date, msg_epoch=RECENT, precedence=1, msg_type="Historical",
           source="satcat"):
    cur.execute(
        "INSERT INTO raw_spacetrack_decay (norad_cat_id, decay_epoch, decay_date, msg_type, "
        "precedence, msg_epoch, source, payload, ingest_run_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, '{}', %s)",
        (norad, date, date, msg_type, precedence, msg_epoch, source, run),
    )


def _sat(cur, norad):
    cur.execute(
        "INSERT INTO satellite (norad_id, canonical_name) VALUES (%s, 'placeholder') "
        "RETURNING satellite_id",
        (norad,),
    )
    sat = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO satellite_identifier (satellite_id, id_type, id_value, source) "
        "VALUES (%s, 'norad', %s, 'satcat')",
        (sat, str(norad)),
    )
    return sat


def _open(cur):
    cur.execute(
        "SELECT source_key, value FROM claim WHERE source = %s AND closed_run IS NULL ORDER BY 1",
        (SRC,),
    )
    return cur.fetchall()


def _decay_date(cur, sat):
    cur.execute("SELECT decay_date FROM satellite WHERE satellite_id = %s", (sat,))
    return cur.fetchone()[0]


def test_space_track_beats_satcat_and_the_satellite_page_lists_its_claim(conn):
    """The spec's criterion: SATCAT says 2024-01-01, Space-Track's actual decay says
    2024-01-02, and the resolver takes Space-Track's (first in precedence.yml), while SATCAT's
    claim stays listed beside it: disagreements are data."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
                    "VALUES ('satcat', 'test', now(), now(), 'ok') RETURNING ingest_run_id")
        sat_run = cur.fetchone()[0]
        st_run = _run(cur)
        sat = _sat(cur, NORAD_A)
        seed_claim(cur, sat, "satcat", "decay_date", "2024-01-01", sat_run, key=str(NORAD_A))
        _decay(cur, st_run, NORAD_A, "2024-01-02")
    assert claims.record_all(conn)[SRC] == (0, 1)
    resolve._resolve_decay_date(conn, resolve.load_precedence()["decay_date"])
    with conn.cursor() as cur:
        assert _decay_date(cur, sat) == dt.date(2024, 1, 2)

    from fastapi.testclient import TestClient

    from api.deps import get_db
    from api.main import app

    conn.row_factory = dict_row
    app.dependency_overrides[get_db] = lambda: conn
    try:
        body = TestClient(app).get(f"/api/satellites/{sat}").json()
    finally:
        app.dependency_overrides.pop(get_db, None)
    decay_claims = {(a["source"], a["value"]) for a in body["assertions"]
                    if a["attribute"] == "decay_date"}
    assert decay_claims == {(SRC, "2024-01-02"), ("satcat", "2024-01-01")}


def test_an_object_that_ages_out_of_the_window_keeps_its_claim(conn):
    """A decay is permanent: a message that left the 30-day window is not a retraction."""
    with conn.cursor() as cur:
        r1 = _run(cur, started=NOW - dt.timedelta(days=20))
        _decay(cur, r1, NORAD_A, "2026-09-25", msg_epoch=AGED)
        _decay(cur, r1, NORAD_B, "2026-10-25")
        r2 = _run(cur)
        _decay(cur, r2, NORAD_B, "2026-10-25")  # A aged out of r2's window
    assert claims.record_spacetrack_decay(conn) == (0, 2)
    with conn.cursor() as cur:
        assert _open(cur) == [(str(NORAD_A), "2026-09-25"), (str(NORAD_B), "2026-10-25")]


def test_a_better_message_that_ages_out_still_wins(conn):
    """The review's scenario: a precedence-1 message ages out while a precedence-2 message for
    the same object stays in the window. The object keeps the precedence-1 date."""
    with conn.cursor() as cur:
        r1 = _run(cur, started=NOW - dt.timedelta(days=20))
        _decay(cur, r1, NORAD_A, "2026-10-01", msg_epoch=AGED, precedence=1)
        _decay(cur, r1, NORAD_A, "2026-10-03", precedence=2)
    assert claims.record_spacetrack_decay(conn) == (0, 1)
    with conn.cursor() as cur:
        r2 = _run(cur)
        _decay(cur, r2, NORAD_A, "2026-10-03", precedence=2)
    assert claims.record_spacetrack_decay(conn) == (0, 0)
    with conn.cursor() as cur:
        assert _open(cur) == [(str(NORAD_A), "2026-10-01")]


def test_a_message_withdrawn_inside_the_window_is_retracted(conn):
    """A message missing from a newer landing whose window covers its epoch was withdrawn: the
    claim closes, and the resolver falls back to SATCAT's date."""
    with conn.cursor() as cur:
        r1 = _run(cur, started=NOW - dt.timedelta(days=1))
        sat = _sat(cur, NORAD_A)
        seed_claim(cur, sat, "satcat", "decay_date", "2026-11-02", r1, key=str(NORAD_A))
        _decay(cur, r1, NORAD_A, "2026-11-01")
        _decay(cur, r1, NORAD_B, "2026-11-03")
    assert claims.record_spacetrack_decay(conn) == (0, 2)
    resolve._resolve_decay_date(conn, resolve.load_precedence()["decay_date"])
    with conn.cursor() as cur:
        assert _decay_date(cur, sat) == dt.date(2026, 11, 1)
        r2 = _run(cur)
        _decay(cur, r2, NORAD_B, "2026-11-03")  # A's message is gone, inside the window
    assert claims.record_spacetrack_decay(conn) == (1, 0)
    resolve._resolve_decay_date(conn, resolve.load_precedence()["decay_date"])
    with conn.cursor() as cur:
        assert _open(cur) == [(str(NORAD_B), "2026-11-03")]
        assert _decay_date(cur, sat) == dt.date(2026, 11, 2)


def test_a_revised_date_replaces_the_old_one(conn):
    with conn.cursor() as cur:
        r1 = _run(cur, started=NOW - dt.timedelta(days=1))
        _decay(cur, r1, NORAD_A, "2026-11-01")
        r2 = _run(cur)
        _decay(cur, r2, NORAD_A, "2026-11-02", msg_epoch=RECENT + dt.timedelta(days=1))
    assert claims.record_spacetrack_decay(conn) == (0, 1)
    with conn.cursor() as cur:
        assert _open(cur) == [(str(NORAD_A), "2026-11-02")]


def test_an_empty_landing_withdraws_nothing(conn):
    """An OK landing with no rows is no evidence that every message was withdrawn."""
    with conn.cursor() as cur:
        r1 = _run(cur, started=NOW - dt.timedelta(days=1))
        _decay(cur, r1, NORAD_A, "2026-11-01")
    assert claims.record_spacetrack_decay(conn) == (0, 1)
    with conn.cursor() as cur:
        _run(cur)
        cur.execute("SELECT count(*) FROM spacetrack_decay_message WHERE withdrawn_run IS NULL")
        assert cur.fetchone()[0] == 1
    claims.record_spacetrack_decay(conn)
    with conn.cursor() as cur:
        assert _open(cur) == [(str(NORAD_A), "2026-11-01")]
        cur.execute("SELECT count(*) FROM spacetrack_decay_message WHERE withdrawn_run IS NULL")
        assert cur.fetchone()[0] == 1


def test_a_message_at_the_window_edge_is_not_called_withdrawn(conn):
    """The landing's window is Space-Track's clock minus 30 days; a message within a day of that
    edge may have just aged out, so absence there is not a withdrawal."""
    with conn.cursor() as cur:
        r1 = _run(cur, started=NOW - dt.timedelta(days=1))
        _decay(cur, r1, NORAD_A, "2026-10-14", msg_epoch=NOW - dt.timedelta(days=29, hours=12))
        _decay(cur, r1, NORAD_B, "2026-11-03")
        r2 = _run(cur)
        _decay(cur, r2, NORAD_B, "2026-11-03")
    assert claims.record_spacetrack_decay(conn) == (0, 2)


def test_one_claim_per_object_the_most_recent_message_wins_with_a_total_order(conn):
    """Lowest PRECEDENCE (Space-Track's "more recent data"), then the newest MSG_EPOCH, then the
    later date, so a tie never flips the claim from night to night. Predictions and messages
    without a parseable date make no claim."""
    with conn.cursor() as cur:
        run = _run(cur)
        _decay(cur, run, NORAD_A, "2026-10-19", precedence=2)
        _decay(cur, run, NORAD_A, "2026-10-21", precedence=1)
        _decay(cur, run, NORAD_A, "2026-10-22", precedence=1)
        _decay(cur, run, NORAD_B, "2026-12-01", msg_type="Prediction")
        _decay(cur, run, NORAD_UNMATCHED, None)
    assert claims.record_spacetrack_decay(conn) == (0, 1)
    with conn.cursor() as cur:
        assert _open(cur) == [(str(NORAD_A), "2026-10-22")]
        cur.execute("SELECT decay_date FROM v_spacetrack_decay_actual WHERE norad_cat_id = %s",
                    (NORAD_A,))
        assert cur.fetchone()[0] == dt.date(2026, 10, 22)


def test_the_first_fold_takes_every_retained_landing_in_order(conn):
    """Bootstrap: with no progress yet, every retained OK landing is folded oldest first, so a
    decay held only by an older monthly keeper still becomes a claim. A failed landing is
    skipped."""
    with conn.cursor() as cur:
        old = _run(cur, started=NOW - dt.timedelta(days=26))
        _decay(cur, old, NORAD_A, "2026-09-20", msg_epoch=NOW - dt.timedelta(days=45))
        failed = _run(cur, started=NOW - dt.timedelta(days=2), status="error")
        _decay(cur, failed, NORAD_UNMATCHED, "2026-11-01")
        new = _run(cur)
        _decay(cur, new, NORAD_B, "2026-11-03")
    assert claims.record_spacetrack_decay(conn) == (0, 2)
    with conn.cursor() as cur:
        assert _open(cur) == [(str(NORAD_A), "2026-09-20"), (str(NORAD_B), "2026-11-03")]
        cur.execute("SELECT last_run FROM claim_progress WHERE source = %s", (SRC,))
        assert cur.fetchone()[0] == new
    assert claims.record_spacetrack_decay(conn) == (0, 0)


def test_a_claim_with_no_satellite_is_kept_but_reaches_no_satellite(conn):
    """An unmatched object is an open claim whose key identifies no satellite; it reaches one
    only through a SATCAT NORAD link, never another source's link with the same key text."""
    with conn.cursor() as cur:
        run = _run(cur)
        _decay(cur, run, NORAD_UNMATCHED, "2026-10-28")
        cur.execute("INSERT INTO satellite (canonical_name) VALUES ('other') "
                    "RETURNING satellite_id")
        other = cur.fetchone()[0]
        cur.execute("INSERT INTO satellite_identifier (satellite_id, id_type, id_value, source) "
                    "VALUES (%s, 'gcat_id', %s, 'gcat')", (other, str(NORAD_UNMATCHED)))
    assert claims.record_spacetrack_decay(conn) == (0, 1)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM v_current_claim WHERE source = %s", (SRC,))
        assert cur.fetchone()[0] == 0
        sat = _sat(cur, NORAD_UNMATCHED)
        cur.execute("SELECT satellite_id, value FROM v_current_claim WHERE source = %s", (SRC,))
        assert cur.fetchall() == [(sat, "2026-10-28")]


def test_no_landing_is_a_no_op(conn):
    assert claims.record_spacetrack_decay(conn) == (0, 0)
    with conn.cursor() as cur:
        _run(cur, status="error")
    assert claims.record_spacetrack_decay(conn) == (0, 0)
