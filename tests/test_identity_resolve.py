"""DB-backed resolver tests: precedence, status fall-through, and SCD2 temporal ownership."""

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_graph  # noqa: E402

from identity import resolve  # noqa: E402

pytestmark = pytest.mark.db

OBS = dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc)


def _run(cur):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
        "VALUES ('satcat', 'test://t4', now(), now(), 'ok') RETURNING ingest_run_id"
    )
    return cur.fetchone()[0]


def _sat(cur, norad, launch=None):
    cur.execute(
        "INSERT INTO satellite (norad_id, canonical_name, launch_date) "
        "VALUES (%s, 'placeholder', %s) RETURNING satellite_id",
        (norad, launch),
    )
    return cur.fetchone()[0]


def _assert_row(cur, sat_id, attribute, value, source, run):
    cur.execute(
        "INSERT INTO source_assertion (satellite_id, source_key, attribute, value, source, "
        "observed_at, ingest_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (sat_id, str(sat_id), attribute, value, source, OBS, run),
    )


def _operator_id(cur, name):
    cur.execute("SELECT operator_id FROM operator WHERE canonical_name = %s", (name,))
    return cur.fetchone()[0]


def test_scalar_precedence_honored_per_attribute(db_conn):
    build_graph.seed_status_map(db_conn)
    with db_conn.cursor() as cur:
        run = _run(cur)
        sat = _sat(cur, 910000301)
        # name precedence [ucs, gcat, satcat] -> ucs wins
        _assert_row(cur, sat, "name", "Commercial Name", "ucs", run)
        _assert_row(cur, sat, "name", "GCAT Name", "gcat", run)
        _assert_row(cur, sat, "name", "SATCAT NAME", "satcat", run)
        # object_type precedence [gcat, satcat] -> gcat wins
        _assert_row(cur, sat, "object_type", "DEBRIS", "gcat", run)
        _assert_row(cur, sat, "object_type", "PAYLOAD", "satcat", run)
        # decay_date precedence [spacetrack_decay, satcat, gcat] -> satcat wins (spacetrack absent)
        _assert_row(cur, sat, "decay_date", "2024-01-01", "satcat", run)
        _assert_row(cur, sat, "decay_date", "2025 Feb 2", "gcat", run)

    resolve.resolve(db_conn)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT canonical_name, object_type, decay_date FROM satellite WHERE satellite_id=%s",
            (sat,),
        )
        name, obj_type, decay = cur.fetchone()
    assert name == "Commercial Name"
    assert obj_type == "DEBRIS"
    assert decay == dt.date(2024, 1, 1)
    db_conn.rollback()


def test_status_precedence_and_unknown_fallthrough(db_conn):
    build_graph.seed_status_map(db_conn)
    with db_conn.cursor() as cur:
        run = _run(cur)
        # GCAT 'R' (DECAYED) is authoritative and wins over SATCAT '+'
        decayed = _sat(cur, 910000311)
        _assert_row(cur, decayed, "status", "R", "gcat", run)
        _assert_row(cur, decayed, "status", "+", "satcat", run)
        # GCAT 'O' maps to UNKNOWN -> falls through to SATCAT '+' (ACTIVE)
        active = _sat(cur, 910000312)
        _assert_row(cur, active, "status", "O", "gcat", run)
        _assert_row(cur, active, "status", "+", "satcat", run)

    resolve.resolve(db_conn)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT canonical_status, source FROM satellite_status_history WHERE satellite_id=%s",
            (decayed,),
        )
        assert cur.fetchone() == ("DECAYED", "gcat")
        cur.execute(
            "SELECT canonical_status, source FROM satellite_status_history WHERE satellite_id=%s",
            (active,),
        )
        assert cur.fetchone() == ("ACTIVE", "satcat")
    db_conn.rollback()


def test_unmapped_status_resolves_unknown_and_is_counted(db_conn):
    build_graph.seed_status_map(db_conn)
    with db_conn.cursor() as cur:
        run = _run(cur)
        sat = _sat(cur, 910000321)
        _assert_row(cur, sat, "status", "ZZZ", "satcat", run)

    stats = resolve.resolve(db_conn)

    assert ("satcat", "ZZZ") in stats["unmapped_status"]
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM satellite_status_history WHERE satellite_id=%s", (sat,)
        )
        assert cur.fetchone()[0] == 0, "unmapped status must not write a resolved history row"
    db_conn.rollback()


def test_scd2_split_for_pre_acquisition_launch(db_conn):
    build_graph.seed_operators(db_conn)
    with db_conn.cursor() as cur:
        run = _run(cur)
        oneweb = _operator_id(cur, "OneWeb")
        eutelsat = _operator_id(cur, "Eutelsat")
        sat = _sat(cur, 910000331, launch=dt.date(2020, 1, 1))  # before 2023-09-28 close
        _assert_row(cur, sat, "owner", "OneWeb", "gcat", run)

    resolve.resolve(db_conn)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT operator_id, valid_from, valid_to FROM satellite_operator "
            "WHERE satellite_id=%s ORDER BY valid_from",
            (sat,),
        )
        rows = cur.fetchall()
    assert rows == [
        (oneweb, dt.date(2020, 1, 1), dt.date(2023, 9, 28)),
        (eutelsat, dt.date(2023, 9, 28), None),
    ]
    db_conn.rollback()


def test_scd2_single_row_for_post_acquisition_launch(db_conn):
    build_graph.seed_operators(db_conn)
    with db_conn.cursor() as cur:
        run = _run(cur)
        oneweb = _operator_id(cur, "OneWeb")
        sat = _sat(cur, 910000341, launch=dt.date(2024, 1, 1))  # after the close date
        _assert_row(cur, sat, "owner", "OneWeb", "gcat", run)

    resolve.resolve(db_conn)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT operator_id, valid_from, valid_to FROM satellite_operator "
            "WHERE satellite_id=%s",
            (sat,),
        )
        rows = cur.fetchall()
    assert rows == [(oneweb, dt.date(2024, 1, 1), None)]
    db_conn.rollback()
