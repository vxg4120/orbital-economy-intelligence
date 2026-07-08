"""Migration + schema tests. All require a reachable dev DB."""

import datetime as dt
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATE_SCRIPT = REPO_ROOT / "scripts" / "migrate.py"


def _run_migrate():
    result = subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.mark.db
def test_runner_applies_cleanly_and_second_run_is_noop(db_conn):
    _run_migrate()
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM schema_migrations")
        count_after_first = cur.fetchone()[0]

    _run_migrate()
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM schema_migrations")
        count_after_second = cur.fetchone()[0]

    assert count_after_first == count_after_second
    assert count_after_first >= 5


@pytest.mark.db
def test_gp_elements_is_a_hypertable(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'gp_elements'"
        )
        row = cur.fetchone()
    assert row is not None, "gp_elements is not registered as a hypertable"


@pytest.mark.db
def test_iss_like_row_generated_columns(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
            "VALUES ('test', 'test', now(), now(), 'ok') RETURNING ingest_run_id"
        )
        run_id = cur.fetchone()[0]
        assert run_id is not None

        cur.execute(
            """
            INSERT INTO gp_elements
                (norad_id, epoch, mean_motion, eccentricity, source)
            VALUES
                (25544, %s, 15.5, 0.0004, 'celestrak_gp')
            RETURNING semi_major_axis_km, perigee_km, apogee_km
            """,
            (dt.datetime.now(dt.timezone.utc),),
        )
        sma, perigee, apogee = cur.fetchone()
    db_conn.rollback()

    assert abs(sma - 6795) <= 15
    assert perigee < apogee


@pytest.mark.db
def test_source_assertion_requires_valid_ingest_run_id(db_conn):
    with db_conn.cursor() as cur:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            cur.execute(
                """
                INSERT INTO source_assertion
                    (source_key, attribute, value, source, observed_at, ingest_run_id)
                VALUES
                    ('99999', 'status', 'ACTIVE', 'satcat', now(), 999999999)
                """
            )
    db_conn.rollback()


@pytest.mark.db
def test_all_norad_columns_are_bigint(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name ILIKE '%norad%'
            """
        )
        rows = cur.fetchall()

    assert rows, "expected at least one %norad% column across the schema"
    non_bigint = [(t, c, d) for t, c, d in rows if d != "bigint"]
    assert not non_bigint, f"non-bigint NORAD columns found: {non_bigint}"
