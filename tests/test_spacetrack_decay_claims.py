"""Space-Track's actual decays as a claims feed (docs/specs/spacetrack-topup.md, the resolver
wiring criterion; migration 0028).

The feed is windowed: each run carries the Historical messages whose MSG_EPOCH falls in the
last 30 days, so an object missing from a run has aged out of the window, not been retracted.
Its claims are keyed by NORAD id and reach a satellite through the SATCAT NORAD link.

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


def _run(cur, source="spacetrack", status="ok"):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
        "VALUES (%s, 'decay', now(), now(), %s) RETURNING ingest_run_id",
        (source, status),
    )
    return cur.fetchone()[0]


def _decay(cur, run, norad, date, msg_type="Historical", precedence=1,
           msg_epoch="2026-11-01T00:00:00Z", source="satcat"):
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
        "SELECT source_key, value FROM claim WHERE source = %s AND closed_run IS NULL "
        "AND source_key::bigint BETWEEN 980000100 AND 980000199 ORDER BY 1",
        (SRC,),
    )
    return cur.fetchall()


def test_space_track_beats_satcat_and_the_satellite_page_lists_its_claim(db_conn):
    """The spec's criterion: SATCAT says 2024-01-01, Space-Track's actual decay says
    2024-01-02, and the resolver takes Space-Track's (first in precedence.yml), while SATCAT's
    claim stays listed beside it: disagreements are data."""
    try:
        with db_conn.cursor() as cur:
            sat_run, st_run = _run(cur, source="satcat"), _run(cur)
            sat = _sat(cur, NORAD_A)
            seed_claim(cur, sat, "satcat", "decay_date", "2024-01-01", sat_run, key=str(NORAD_A))
            _decay(cur, st_run, NORAD_A, "2024-01-02")
        assert claims.record_spacetrack_decay(db_conn) == (0, 1)
        resolve._resolve_decay_date(db_conn, resolve.load_precedence()["decay_date"])
        with db_conn.cursor() as cur:
            cur.execute("SELECT decay_date FROM satellite WHERE satellite_id = %s", (sat,))
            assert cur.fetchone()[0] == dt.date(2024, 1, 2)

        from fastapi.testclient import TestClient

        from api.deps import get_db
        from api.main import app

        db_conn.row_factory = dict_row
        app.dependency_overrides[get_db] = lambda: db_conn
        try:
            body = TestClient(app).get(f"/api/satellites/{sat}").json()
        finally:
            app.dependency_overrides.pop(get_db, None)
        decay_claims = {(a["source"], a["value"]) for a in body["assertions"]
                        if a["attribute"] == "decay_date"}
        assert decay_claims == {(SRC, "2024-01-02"), ("satcat", "2024-01-01")}
    finally:
        db_conn.row_factory = None
        db_conn.rollback()


def test_an_object_that_ages_out_of_the_window_keeps_its_claim(db_conn):
    """A decay is permanent; leaving the 30-day window is not a retraction. Only a new date for
    the same object closes the old one."""
    try:
        with db_conn.cursor() as cur:
            r1, r2 = _run(cur), _run(cur)
            _decay(cur, r1, NORAD_A, "2026-10-20")
            _decay(cur, r1, NORAD_B, "2026-10-25")
        assert claims.record_spacetrack_decay(db_conn) == (0, 2)
        with db_conn.cursor() as cur:
            _decay(cur, r2, NORAD_B, "2026-10-26")  # A aged out; B was revised
        assert claims.record_spacetrack_decay(db_conn) == (1, 1)
        with db_conn.cursor() as cur:
            assert _open(cur) == [(str(NORAD_A), "2026-10-20"), (str(NORAD_B), "2026-10-26")]
            cur.execute("SELECT value, first_run, closed_run FROM claim WHERE source = %s "
                        "AND source_key = %s ORDER BY first_run", (SRC, str(NORAD_B)))
            assert cur.fetchall() == [("2026-10-25", r1, r2), ("2026-10-26", r2, None)]
    finally:
        db_conn.rollback()


def test_one_claim_per_object_the_most_recent_message_wins(db_conn):
    """Several Historical messages for one object: the lowest PRECEDENCE (Space-Track's "more
    recent data"), then the newest MSG_EPOCH, as v_spacetrack_decay_actual has it. Predictions
    and messages without a parseable date make no claim."""
    try:
        with db_conn.cursor() as cur:
            run = _run(cur)
            _decay(cur, run, NORAD_A, "2026-10-19", precedence=2)
            _decay(cur, run, NORAD_A, "2026-10-21", precedence=1, msg_epoch="2026-10-30T00:00:00Z")
            _decay(cur, run, NORAD_A, "2026-10-22", precedence=1, msg_epoch="2026-10-31T00:00:00Z")
            _decay(cur, run, NORAD_B, "2026-12-01", msg_type="Prediction")
            _decay(cur, run, NORAD_UNMATCHED, None)
        assert claims.record_spacetrack_decay(db_conn) == (0, 1)
        with db_conn.cursor() as cur:
            assert _open(cur) == [(str(NORAD_A), "2026-10-22")]
    finally:
        db_conn.rollback()


def test_a_claim_with_no_satellite_is_kept_but_reaches_no_satellite(db_conn):
    """An object the graph has not matched still gets its claim (an unmatched object is an open
    claim whose key identifies no satellite); v_current_claim shows it only once a SATCAT NORAD
    link exists, and never through another source's link with the same key text."""
    try:
        with db_conn.cursor() as cur:
            run = _run(cur)
            _decay(cur, run, NORAD_UNMATCHED, "2026-10-28")
            # A GCAT link whose key text happens to equal the NORAD id must not carry the claim.
            cur.execute("INSERT INTO satellite (canonical_name) VALUES ('other') "
                        "RETURNING satellite_id")
            other = cur.fetchone()[0]
            cur.execute("INSERT INTO satellite_identifier (satellite_id, id_type, id_value, "
                        "source) VALUES (%s, 'gcat_id', %s, 'gcat')",
                        (other, str(NORAD_UNMATCHED)))
        assert claims.record_spacetrack_decay(db_conn) == (0, 1)
        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM v_current_claim WHERE source = %s "
                        "AND source_key = %s", (SRC, str(NORAD_UNMATCHED)))
            assert cur.fetchone()[0] == 0
            sat = _sat(cur, NORAD_UNMATCHED)
            cur.execute("SELECT satellite_id, value FROM v_current_claim WHERE source = %s "
                        "AND source_key = %s", (SRC, str(NORAD_UNMATCHED)))
            assert cur.fetchall() == [(sat, "2026-10-28")]
    finally:
        db_conn.rollback()


def test_a_run_recorded_twice_changes_nothing_and_no_run_is_a_no_op(db_conn):
    try:
        with db_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM claim_progress WHERE source = %s", (SRC,))
            fresh = cur.fetchone() is None
            failed = _run(cur, status="error")
            _decay(cur, failed, NORAD_A, "2026-10-20")
        if fresh:
            # Only a failed run on file: nothing to record.
            assert claims.record_spacetrack_decay(db_conn) == (0, 0)
        with db_conn.cursor() as cur:
            run = _run(cur)
            _decay(cur, run, NORAD_A, "2026-10-21")
        assert claims.record_spacetrack_decay(db_conn) == (0, 1)
        assert claims.record_spacetrack_decay(db_conn) == (0, 0)
    finally:
        db_conn.rollback()


def test_the_nightly_records_the_feed():
    assert SRC in claims.SOURCES
    assert resolve.load_precedence()["decay_date"][0] == SRC
