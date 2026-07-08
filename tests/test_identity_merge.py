"""DB-backed merge tests: repoint every child row, log the merge, leave no orphans."""

import datetime as dt

import pytest

from identity import merge

pytestmark = pytest.mark.db


def _run(cur):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
        "VALUES ('satcat', 'test://t4', now(), now(), 'ok') RETURNING ingest_run_id"
    )
    return cur.fetchone()[0]


def _sat(cur, norad, name):
    cur.execute(
        "INSERT INTO satellite (norad_id, canonical_name) VALUES (%s, %s) RETURNING satellite_id",
        (norad, name),
    )
    return cur.fetchone()[0]


def _operator(cur):
    cur.execute(
        "INSERT INTO operator (canonical_name, country, operator_class) "
        "VALUES ('T4 Test Operator', 'US', 'commercial') RETURNING operator_id"
    )
    return cur.fetchone()[0]


def test_merge_repoints_children_logs_and_leaves_no_orphans(db_conn):
    obs = dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc)
    with db_conn.cursor() as cur:
        run = _run(cur)
        op = _operator(cur)
        surviving = _sat(cur, 910000101, "Survivor")
        merged = _sat(cur, 910000102, "Doomed")

        # child rows hanging off the merged shell
        cur.execute(
            "INSERT INTO satellite_identifier (satellite_id, id_type, id_value, source) "
            "VALUES (%s, 'name_gcat', 'Doomed Name', 'gcat')",
            (merged,),
        )
        cur.execute(
            "INSERT INTO source_assertion (satellite_id, source_key, attribute, value, source, "
            "observed_at, ingest_run_id) VALUES (%s, '910000102', 'owner', 'X', 'gcat', %s, %s)",
            (merged, obs, run),
        )
        cur.execute(
            "INSERT INTO satellite_status_history (satellite_id, canonical_status, observed_at, "
            "source) VALUES (%s, 'ACTIVE', %s, 'gcat')",
            (merged, obs),
        )
        cur.execute(
            "INSERT INTO satellite_operator (satellite_id, operator_id, role, valid_from, source) "
            "VALUES (%s, %s, 'owner', '2020-01-01', 'seed')",
            (merged, op),
        )
        # a status-history row on BOTH satellites with the same natural key -> exercises dedup
        cur.execute(
            "INSERT INTO satellite_status_history (satellite_id, canonical_status, observed_at, "
            "source) VALUES (%s, 'INACTIVE', %s, 'satcat')",
            (surviving, obs),
        )
        cur.execute(
            "INSERT INTO satellite_status_history (satellite_id, canonical_status, observed_at, "
            "source) VALUES (%s, 'DECAYED', %s, 'satcat')",
            (merged, obs),
        )

    merge.merge(db_conn, surviving, merged, "norad_dup_test", 0.99, details={"why": "test"})

    with db_conn.cursor() as cur:
        # merged shell is gone
        cur.execute("SELECT count(*) FROM satellite WHERE satellite_id = %s", (merged,))
        assert cur.fetchone()[0] == 0

        # no orphan child rows reference the merged id anywhere
        for table in (
            "satellite_identifier",
            "source_assertion",
            "satellite_status_history",
            "satellite_operator",
        ):
            cur.execute(f"SELECT count(*) FROM {table} WHERE satellite_id = %s", (merged,))
            assert cur.fetchone()[0] == 0, f"orphan rows left in {table}"

        # children repointed onto the survivor
        cur.execute(
            "SELECT count(*) FROM satellite_identifier WHERE satellite_id=%s AND id_value=%s",
            (surviving, "Doomed Name"),
        )
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM source_assertion WHERE satellite_id=%s", (surviving,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM satellite_operator WHERE satellite_id=%s", (surviving,))
        assert cur.fetchone()[0] == 1
        # the survivor kept its own conflicting status row; the merged duplicate was dropped
        cur.execute(
            "SELECT canonical_status FROM satellite_status_history "
            "WHERE satellite_id=%s AND source='satcat' AND observed_at=%s",
            (surviving, obs),
        )
        assert cur.fetchone()[0] == "INACTIVE"

        # the merge is audited
        cur.execute(
            "SELECT rule_fired FROM merge_log WHERE surviving_id=%s AND merged_id=%s",
            (surviving, merged),
        )
        assert cur.fetchone()[0] == "norad_dup_test"
    db_conn.rollback()
