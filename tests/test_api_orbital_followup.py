"""Isolated audit regressions; no default/shared database connections.

The db-marked tests use TEMP tables and run against a disposable local server.
All other cases use the real routes/cache with a recording database stand-in.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from api import cache
from api.deps import get_db
from api.routers import conflicts, operators


class RecordingDB:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.statements = []
        self.closed = 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchall(self):
        params = self.statements[-1][1]
        if isinstance(params, tuple):
            limit, offset = params
            return list(self.rows[offset:offset + limit])
        return list(self.rows)

    def fetchone(self):
        return {"total": len(self.rows), "n": len(self.rows), "with_fleet": len(self.rows)}

    def close(self):
        self.closed += 1


def client_for(router, db):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_active_sort_is_supported_and_invalid_sorts_never_reach_sql():
    db = RecordingDB()
    client = client_for(operators.router, db)
    response = client.get("/api/operators", params={"sort": "active"})
    assert response.status_code == 200
    assert response.json() == {"rows": [], "total": 0, "with_fleet": 0}
    statement_count = len(db.statements)
    for bad_sort in ("bogus", "fleet_active DESC; DROP TABLE operator", "ACTIVE"):
        assert client.get("/api/operators", params={"sort": bad_sort}).status_code == 422
    assert len(db.statements) == statement_count


@pytest.mark.parametrize("path,count", [
    ("status", conflicts.count_status_conflicts),
    ("stale-owners", conflicts.count_stale_owners),
])
def test_disabled_cache_computes_each_page_once(path, count):
    db = RecordingDB([{"satellite_id": n} for n in range(5)])
    client = client_for(conflicts.router, db)
    body = client.get(f"/api/conflicts/{path}?limit=2&offset=1").json()
    assert body == {"rows": [{"satellite_id": 1}, {"satellite_id": 2}], "total": 5}
    assert len(db.statements) == 1, "one full CTE, not count + page CTE"
    db.rows = []
    assert count(db) == 0, "disabled caching must see the request's current data"


@pytest.mark.parametrize("path,cache_name,count", [
    ("status", "_status_rows_cache", conflicts.count_status_conflicts),
    ("stale-owners", "_stale_rows_cache", conflicts.count_stale_owners),
])
@pytest.mark.parametrize("size", [0, 5])
def test_warm_conflict_pages_and_count_share_one_snapshot(monkeypatch, path, cache_name, count, size):
    db = RecordingDB([{"satellite_id": n} for n in range(size)])
    monkeypatch.setattr(cache, "_enabled", True)
    monkeypatch.setattr(cache, "_read_only_conn", lambda: db)
    row_cache = getattr(conflicts, cache_name)
    monkeypatch.setattr(row_cache, "_has_value", False)
    monkeypatch.setattr(row_cache, "_value", None)
    client = client_for(conflicts.router, db)
    pages = [client.get(f"/api/conflicts/{path}?limit=2&offset={offset}").json()
             for offset in (0, 2, 4, 6)]
    assert [row for page in pages for row in page["rows"]] == db.rows
    assert all(page["total"] == size for page in pages)
    assert pages[-1]["rows"] == []
    assert count(db) == size
    assert len(db.statements) == 1
    assert db.closed == 1
    db.rows = [{"satellite_id": 99}]
    assert count(db) == size, "count and list retain the same warm snapshot"
    row_cache._refresh()
    assert count(db) == 1
    assert client.get(f"/api/conflicts/{path}").json() == {
        "rows": [{"satellite_id": 99}], "total": 1}
    assert len(db.statements) == 2


def test_cache_cold_requests_share_background_refresh(monkeypatch):
    monkeypatch.setattr(cache, "_enabled", True)
    db = RecordingDB()
    monkeypatch.setattr(cache, "_read_only_conn", lambda: db)
    computing, release, duplicate = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def compute(_db):
        calls.append(1)
        if len(calls) > 1:
            duplicate.set()
        computing.set()
        assert release.wait(5)
        return []

    row_cache = cache.WarmCache("cold-test", compute)
    with ThreadPoolExecutor(max_workers=4) as pool:
        background = pool.submit(row_cache._refresh)
        assert computing.wait(5)
        readers = [pool.submit(row_cache.get, None) for _ in range(3)]
        duplicated = duplicate.wait(0.2)
        release.set()
        assert background.result(timeout=5) == []
        assert all(reader.result(timeout=5) == [] for reader in readers)
    assert not duplicated
    assert len(calls) == db.closed == 1


def test_cache_failed_refresh_keeps_warm_reads_nonblocking_and_recovers(monkeypatch):
    monkeypatch.setattr(cache, "_enabled", True)
    db = RecordingDB()
    monkeypatch.setattr(cache, "_read_only_conn", lambda: db)
    computing, release = threading.Event(), threading.Event()
    values = iter(([{"satellite_id": 1}], RuntimeError("refresh unavailable"), []))

    def compute(_db):
        value = next(values)
        if isinstance(value, Exception):
            computing.set()
            assert release.wait(5)
            raise value
        return value

    row_cache = cache.WarmCache("failure-test", compute)
    assert row_cache.get(None) == [{"satellite_id": 1}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(row_cache._refresh)
        assert computing.wait(5)
        reader = pool.submit(row_cache.get, None)
        try:
            assert reader.result(timeout=1) == [{"satellite_id": 1}]
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="refresh unavailable"):
            refresh.result(timeout=5)
    assert row_cache.get(None) == [{"satellite_id": 1}]
    assert row_cache._refresh() == []
    assert row_cache.get(None) == []
    assert db.closed == 3


def test_cache_failed_initial_compute_can_retry(monkeypatch):
    monkeypatch.setattr(cache, "_enabled", True)
    db = RecordingDB()
    monkeypatch.setattr(cache, "_read_only_conn", lambda: db)
    outcomes = iter((RuntimeError("cold failure"), []))

    def compute(_db):
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    row_cache = cache.WarmCache("cold-failure-test", compute)
    with pytest.raises(RuntimeError, match="cold failure"):
        row_cache.get(None)
    assert row_cache.get(None) == []
    assert db.closed == 2


@pytest.mark.db
def test_active_sort_orders_actual_sql_with_ties_and_zero_fleets(db_conn):
    """Active order differs from fleet, name, and ID order; active ties cross a page boundary."""
    db_conn.row_factory = dict_row
    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE operator (
                operator_id int PRIMARY KEY, canonical_name text, country text, operator_class text);
            CREATE TEMP TABLE satellite_operator (
                operator_id int, satellite_id int, role text, valid_to date);
            CREATE TEMP TABLE satellite_status_history (
                satellite_id int, canonical_status text, observed_at timestamptz);
            CREATE TEMP TABLE operator_relationship (
                parent_id int, child_id int, valid_from date, valid_to date);
            INSERT INTO operator VALUES
                (40, 'Delta empty', 'US', 'private'), (30, 'Beta most active', 'US', 'private'),
                (20, 'Alpha largest fleet', 'US', 'private'), (10, 'Gamma tie first', 'US', 'private');
            INSERT INTO satellite_operator
                SELECT 30, n, 'owner', NULL FROM generate_series(301, 303) n;
            INSERT INTO satellite_operator
                SELECT 20, n, 'owner', NULL FROM generate_series(201, 206) n;
            INSERT INTO satellite_operator
                SELECT 10, n, 'owner', NULL FROM generate_series(101, 105) n;
            INSERT INTO satellite_status_history
                SELECT satellite_id,
                    CASE WHEN operator_id = 30 OR satellite_id % 100 <= 2 THEN 'ACTIVE'
                         ELSE 'INACTIVE' END,
                    now()
                FROM satellite_operator;
        """)
    client = client_for(operators.router, db_conn)
    pages = [client.get(f"/api/operators?sort=active&limit=2&offset={offset}")
             for offset in (0, 2, 4)]
    assert all(response.status_code == 200 for response in pages)
    bodies = [response.json() for response in pages]
    rows = [row for body in bodies for row in body["rows"]]
    assert [(row["operator_id"], row["fleet_active"]) for row in rows] == [
        (30, 3), (10, 2), (20, 2), (40, 0)]
    assert [[row["operator_id"] for row in body["rows"]] for body in bodies] == [
        [30, 10], [20, 40], []]
    # Make the competing orders explicit so this fixture cannot accidentally become
    # non-discriminating again (the previous equal fleet sizes masked a wrong mapping).
    fleet = client.get("/api/operators?sort=fleet").json()["rows"]
    name = client.get("/api/operators?sort=name").json()["rows"]
    assert [row["operator_id"] for row in fleet] == [20, 10, 30, 40]
    assert [row["operator_id"] for row in name] == [20, 30, 40, 10]
    assert [row["fleet_total"] for row in rows] == [3, 5, 6, 0]
    assert all(body["total"] == 4 and body["with_fleet"] == 3 for body in bodies)
    assert bodies[-1]["rows"] == []


def test_background_cadence_retries_failure_and_preserves_last_value(monkeypatch):
    monkeypatch.setattr(cache, "_enabled", True)
    db = RecordingDB()
    monkeypatch.setattr(cache, "_read_only_conn", lambda: db)
    outcomes = iter(([1], RuntimeError("refresh failed"), []))
    sleeps, snapshots = [], []

    def compute(_db):
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value

    class StopLoop(BaseException):
        pass

    row_cache = cache.WarmCache("timer-test", compute)

    def sleep(delay):
        sleeps.append(delay)
        snapshots.append(row_cache.get(None))
        if len(sleeps) == 3:
            raise StopLoop

    monkeypatch.setattr(cache.time, "sleep", sleep)
    with pytest.raises(StopLoop):
        row_cache._loop()
    assert sleeps == [900.0, 60.0, 900.0]
    assert snapshots == [[1], [1], []]
    assert db.closed == 3


def test_startup_after_inline_compute_reuses_first_payload(monkeypatch):
    monkeypatch.setattr(cache, "_enabled", True)
    db = RecordingDB()
    monkeypatch.setattr(cache, "_read_only_conn", lambda: db)
    row_cache = cache.WarmCache("startup-test", lambda _db: [])
    assert row_cache.get(None) == []

    class StopLoop(BaseException):
        pass

    def stop(_delay):
        raise StopLoop

    monkeypatch.setattr(cache.time, "sleep", stop)
    with pytest.raises(StopLoop):
        row_cache._loop()
    assert db.closed == 1


@pytest.mark.db
def test_conflict_sql_keeps_order_provenance_and_counts(db_conn):
    db_conn.row_factory = dict_row
    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE source_assertion (
                satellite_id int, source text, attribute text, value text,
                observed_at timestamptz, ingest_run_id int, source_key text);
            CREATE TEMP TABLE status_mapping (source text, source_value text, canonical_status text);
            CREATE TEMP TABLE satellite (satellite_id int PRIMARY KEY, norad_id int, canonical_name text);
            CREATE TEMP TABLE operator (operator_id int PRIMARY KEY, canonical_name text);
            CREATE TEMP TABLE operator_alias (operator_id int, source text, alias text);
            CREATE TEMP TABLE operator_relationship (
                parent_id int, child_id int, relationship text, valid_from date, valid_to date);
            INSERT INTO satellite VALUES (3, NULL, 'Null NORAD'), (2, 20, 'Second'),
                (1, 10, 'First'), (4, 40, 'Unknown status'), (5, 50, 'Agreement');
            INSERT INTO status_mapping VALUES
                ('satcat', '+', 'ACTIVE'), ('satcat', '?', 'UNKNOWN'),
                ('gcat', 'D', 'DECAYED'), ('gcat', '+', 'ACTIVE');
            INSERT INTO source_assertion
            SELECT satellite_id, 'satcat', 'status', CASE WHEN satellite_id = 4 THEN '?' ELSE '+' END,
                now(), 1, satellite_id::text FROM satellite;
            INSERT INTO source_assertion
            SELECT satellite_id, 'gcat', 'status', CASE WHEN satellite_id = 5 THEN '+' ELSE 'D' END,
                now(), 1, satellite_id::text FROM satellite;
            INSERT INTO operator VALUES (1, 'Original'), (2, 'Acquirer');
            INSERT INTO operator_alias VALUES (1, 'satcat', 'old');
            INSERT INTO operator_relationship VALUES
                (2, 1, 'acquired_by', current_date - 10, NULL);
            INSERT INTO source_assertion
            SELECT satellite_id, 'satcat', 'owner', 'OLD', now(), 1, satellite_id::text
                FROM satellite WHERE satellite_id <= 3;
        """)
    client = client_for(conflicts.router, db_conn)
    for path, count in (("status", conflicts.count_status_conflicts),
                        ("stale-owners", conflicts.count_stale_owners)):
        pages = [client.get(f"/api/conflicts/{path}?limit=2&offset={offset}").json()
                 for offset in (0, 2, 4)]
        rows = [row for page in pages for row in page["rows"]]
        assert [row["satellite_id"] for row in rows] == [1, 2, 3]
        assert all(page["total"] == 3 for page in pages)
        assert count(db_conn) == 3
        if path == "status":
            assert all(row["satcat_status"] == "ACTIVE" and row["gcat_status"] == "DECAYED"
                       for row in rows)
        else:
            assert all(row["catalog_owner"] == "OLD" and row["resolved_operator"] == "Original"
                       and row["acquired_by"] == "Acquirer" for row in rows)
