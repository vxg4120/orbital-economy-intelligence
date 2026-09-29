"""Retention for the per-run snapshot tables (scripts/prune_snapshots.py).

The policy is a pure function over the runs present in one table, so most of it is pinned here
without a database. The db-marked tests drive the two write paths, the nightly DELETE and the
one-time TRUNCATE-and-reinsert, against a scratch table inside a transaction that is rolled back.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.prune_snapshots import (
    KEEP_LATEST,
    SNAPSHOT_TABLES,
    Run,
    compact,
    delete_runs,
    runs_in,
    runs_to_drop,
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


def _run(run_id, month, day, status="ok", stream=None, rows=10):
    return Run(stream, run_id, status, datetime(2026, month, day, 7, 10, tzinfo=UTC), rows)


def _ids(runs):
    return sorted(r.run_id for r in runs)


def test_keeps_the_newest_three_ok_runs_and_each_months_first():
    runs = [_run(1, 7, 8), _run(2, 7, 9), _run(3, 8, 3), _run(4, 8, 4), _run(5, 8, 5),
            _run(6, 9, 1), _run(7, 9, 2), _run(8, 9, 3), _run(9, 9, 4)]
    # 1, 3 and 6 open July, August and September; 7, 8 and 9 are the newest three.
    assert _ids(runs_to_drop(runs)) == [2, 4, 5]


def test_a_month_is_a_utc_month():
    # 23:30 on Aug 31 in Los Angeles is already September in UTC, so this run opens September.
    late = Run(None, 2, "ok", datetime.fromisoformat("2026-08-31T23:30:00-07:00"), 10)
    runs = [_run(1, 8, 1), late] + [_run(i, 9, i) for i in range(3, 7)]
    assert _ids(runs_to_drop(runs)) == [3]


def test_a_run_newer_than_the_newest_ok_run_is_never_touched():
    # Run 7 is an ingest still in flight (no status yet): no reader sees it, and it is not ours
    # to judge. Run 2 failed long ago, and no reader ever selects a failed run.
    runs = [_run(1, 9, 1), _run(2, 9, 2, status="error"), _run(3, 9, 3), _run(4, 9, 4),
            _run(5, 9, 5), _run(6, 9, 6), _run(7, 9, 7, status=None)]
    assert _ids(runs_to_drop(runs)) == [2, 3]


def test_each_stream_keeps_its_own_runs():
    # source_assertion interleaves the satcat and gcat runs; counting the newest three across
    # both would leave one of them with a single run and blind churn detection.
    runs = [_run(i, 9, i, stream="satcat" if i % 2 else "gcat") for i in range(1, 13)]
    dropped = {(r.stream, r.run_id) for r in runs_to_drop(runs, prunable=("satcat", "gcat"))}
    kept = {(r.stream, r.run_id) for r in runs} - dropped
    # Each keeps its own newest three and its own first of the month.
    assert kept == {("satcat", 1), ("satcat", 7), ("satcat", 9), ("satcat", 11),
                    ("gcat", 2), ("gcat", 8), ("gcat", 10), ("gcat", 12)}


def test_a_stream_outside_the_prunable_ones_is_kept_whole():
    # An operator correction is asserted once and never again; ageing it out would lose it.
    runs = [_run(i, 9, i, stream="operator_confirmed") for i in range(1, 8)]
    assert runs_to_drop(runs, prunable=("satcat", "gcat", "ucs")) == []


def test_a_stream_with_no_ok_run_keeps_everything():
    runs = [_run(1, 9, 1, status="error"), _run(2, 9, 2, status=None),
            Run(None, 3, None, None, 10)]
    assert runs_to_drop(runs) == []


def test_fewer_than_two_kept_runs_is_refused():
    # identity/churn.py compares the two newest OK raw_gcat_satcat runs.
    assert KEEP_LATEST >= 2
    with pytest.raises(ValueError, match="churn"):
        runs_to_drop([_run(1, 9, 1)], keep_latest=1)


def test_every_raw_table_in_the_migrations_is_pruned():
    """A raw_* table left off SNAPSHOT_TABLES grows by a full copy of its source every run,
    which is how the disk reached 93% on 2026-09-28."""
    created = set()
    for sql in MIGRATIONS.glob("*.sql"):
        created |= set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(raw_\w+)", sql.read_text()))
    assert created, "no raw_* tables found; did the migrations move?"
    assert created <= set(SNAPSHOT_TABLES), f"not pruned: {sorted(created - set(SNAPSHOT_TABLES))}"


def test_source_assertion_prunes_only_the_feeds_that_reassert_every_run():
    # identity/assertions.py re-asserts these three on every run; nothing else re-asserts.
    assert SNAPSHOT_TABLES["source_assertion"] == ("source", ("satcat", "gcat", "ucs"))


SPLIT = ("source", ("satcat", "gcat"))


def _scratch(conn):
    """A scratch snapshot table shaped like source_assertion: a GENERATED ALWAYS identity, a
    source column, and a generated column the rewrite must skip. satcat has rows in five OK runs
    across two months and one run in flight; gcat only in aug2 and sep3; an operator correction
    in aug2. Returns {run label: ingest_run_id}."""
    ids = {}
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE raw_prune_scratch ("
            " row_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
            " source TEXT NOT NULL, ingest_run_id BIGINT NOT NULL, payload TEXT,"
            " payload_len INT GENERATED ALWAYS AS (length(payload)) STORED)"
        )
        for label, started, status in [
            ("aug1", "2026-08-01", "ok"), ("aug2", "2026-08-02", "ok"),
            ("sep1", "2026-09-01", "ok"), ("sep2", "2026-09-02", "ok"),
            ("sep3", "2026-09-03", "ok"), ("flight", "2026-09-04", None),
        ]:
            cur.execute(
                "INSERT INTO ingest_run (source, endpoint, started_at, status) "
                "VALUES ('prune_test', 'scratch', %s, %s) RETURNING ingest_run_id",
                (started, status),
            )
            ids[label] = cur.fetchone()[0]
        for source, labels in [("satcat", list(ids)), ("gcat", ["aug2", "sep3"]),
                               ("operator_confirmed", ["aug2"])]:
            for label in labels:
                cur.execute(
                    "INSERT INTO raw_prune_scratch (source, ingest_run_id, payload) "
                    "SELECT %s, %s, %s || '-' || g FROM generate_series(1, 3) g",
                    (source, ids[label], label),
                )
    return ids


def _rows(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT row_id, source, ingest_run_id, payload, payload_len "
                    "FROM raw_prune_scratch ORDER BY row_id")
        return cur.fetchall()


@pytest.mark.db
def test_compact_drops_exactly_the_dropped_pairs_and_keeps_ids(db_conn):
    ids = _scratch(db_conn)
    before = _rows(db_conn)
    runs = runs_in(db_conn, "raw_prune_scratch", SPLIT)
    drop = runs_to_drop(runs, SPLIT[1])
    # aug2 is dropped for satcat but is one of gcat's newest runs, and the operator correction
    # in the same run is not prunable at all: the rewrite must tell the three apart.
    assert [(r.stream, r.run_id) for r in drop] == [("satcat", ids["aug2"])]

    assert compact(db_conn, "raw_prune_scratch", runs, drop, SPLIT) == len(before) - 3
    # Byte-identical rows, identities included, for everything but the dropped pair.
    assert _rows(db_conn) == [
        row for row in before if (row[1], row[2]) != ("satcat", ids["aug2"])
    ]
    # The identity sequence was not reset, so a new row cannot collide with a kept one.
    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO raw_prune_scratch (source, ingest_run_id, payload) "
                    "VALUES ('satcat', %s, 'new') RETURNING row_id", (ids["sep3"],))
        assert cur.fetchone()[0] > max(row[0] for row in before)
    db_conn.rollback()


@pytest.mark.db
def test_delete_drops_exactly_the_dropped_pairs(db_conn):
    ids = _scratch(db_conn)
    before = _rows(db_conn)
    runs = runs_in(db_conn, "raw_prune_scratch", SPLIT)
    drop = runs_to_drop(runs, SPLIT[1])

    assert delete_runs(db_conn, "raw_prune_scratch", drop, SPLIT) == 3
    assert _rows(db_conn) == [
        row for row in before if (row[1], row[2]) != ("satcat", ids["aug2"])
    ]
    db_conn.rollback()


@pytest.mark.db
def test_an_unsplit_table_is_one_stream(db_conn):
    ids = _scratch(db_conn)
    before = _rows(db_conn)
    runs = runs_in(db_conn, "raw_prune_scratch")
    assert {r.stream for r in runs} == {None}
    drop = runs_to_drop(runs)
    assert [r.run_id for r in drop] == [ids["aug2"]]

    # Unsplit, every row of the dropped run goes, whatever its source column says.
    assert compact(db_conn, "raw_prune_scratch", runs, drop) == len(before) - 9
    assert _rows(db_conn) == [row for row in before if row[2] != ids["aug2"]]
    db_conn.rollback()
