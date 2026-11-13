"""Contract + known-data tests for GET /api/stats (Task F1).

DB-marked: runs a FastAPI TestClient against the live dev DB (read-only, seeds nothing). Asserts
the exact field names from the spec's API contract plus spot checks on real data.
"""

import warnings

import pytest
from psycopg.rows import dict_row

# TestClient's transport import emits a StarletteDeprecationWarning at import time; suppress it
# here so the module imports cleanly under `pytest -W error` (it is not our warning to fix).
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app


@pytest.fixture
def client(db_conn):  # db_conn: skips the whole module when the dev DB is unreachable
    return TestClient(app)


@pytest.mark.db
@pytest.mark.graph
def test_stats_shape_and_spot_checks(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()

    for key in ("satellites", "in_orbit_objects", "on_orbit_payloads", "operators",
                "identifier_rows", "merge_events", "gp_elements", "coverage", "conflicts",
                "ingest_runs"):
        assert key in body, f"missing top-level key {key}"

    # Spot check: the real build has > 60k satellites.
    assert isinstance(body["satellites"], int)
    assert body["satellites"] > 60000
    # The catalog keeps reentered objects, so the in-orbit subset is smaller, and it holds
    # every on-orbit payload.
    assert body["on_orbit_payloads"] <= body["in_orbit_objects"] < body["satellites"]

    cov = body["coverage"]
    # multi_source_pct stays for the clients that read it; both_catalogs_pct is the new meter.
    assert set(cov) == {"operator_pct", "status_pct", "multi_source_pct", "both_catalogs_pct"}
    for name, pct in cov.items():
        assert isinstance(pct, float), f"{name} must be a float"
        assert 0.0 <= pct <= 100.0, f"{name} out of range: {pct}"
    assert cov["both_catalogs_pct"] <= cov["multi_source_pct"]

    conflicts = body["conflicts"]
    assert set(conflicts) == {"status", "decay", "stale_owners"}
    assert all(isinstance(v, int) for v in conflicts.values())
    assert conflicts["status"] >= 30  # SATCAT/GCAT status disagreements in the real build

    assert isinstance(body["ingest_runs"], list)
    assert body["ingest_runs"], "ingest ledger should not be empty"
    for run in body["ingest_runs"]:
        assert set(run) == {"source", "endpoint", "status", "finished_at", "rows_ingested"}


@pytest.mark.db
def test_stats_normalizes_inflight_spacetrack_runs(db_conn):
    """The black-page regression: the Space-Track backfill writes one ingest_run row PER 100-NORAD
    batch, with the full query URL as `endpoint` and a NULL status while in flight. Unnormalized
    that floods the ledger with thousands of rows and leaks nulls the SPA crashes on. The endpoint
    must inject a synthetic in-flight row (rolled back, never committed) and prove the payload is
    collapsed + null-safe.
    """
    db_conn.row_factory = dict_row
    # Exactly how the backfill writes it: NULL status/finished_at, the whole 100-NORAD query URL
    # as endpoint. Reserved-range NORAD ids so the string can never collide with real forensics.
    giant_endpoint = (
        "/basicspacedata/query/class/gp_history/NORAD_CAT_ID/"
        + ",".join(str(n) for n in range(900000001, 900000101))
        + "/CREATION_DATE/2025-01-01--2025-02-01/orderby/CREATION_DATE asc/format/json"
    )
    with db_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingest_run (source, endpoint, started_at) "
            "VALUES ('spacetrack', %s, now())",
            (giant_endpoint,),
        )
    # Deliberately NOT committed -> visible to this connection's transaction only, rolled back
    # at teardown so the shared dev DB (and the live backfill) is left untouched.

    app.dependency_overrides[get_db] = lambda: db_conn
    try:
        body = TestClient(app).get("/api/stats").json()
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_conn.rollback()

    runs = body["ingest_runs"]
    assert runs, "ingest ledger should not be empty"
    # 1. No null statuses ever reach the SPA (the actual black-page root cause).
    assert all(r["status"] is not None for r in runs), "null status leaked into the payload"
    # 2. The giant per-batch URL is collapsed to the stable class label 'gp_history'.
    labels = {r["endpoint"] for r in runs}
    assert "gp_history" in labels
    assert all("NORAD_CAT_ID" not in r["endpoint"] for r in runs), "raw batch URL leaked"
    # 3. The in-flight (recent, NULL-status) batch reads as 'running', not null. Completed backfill
    #    batches collapse to the same label with status 'ok', so gp_history spans several statuses;
    #    the freshly-started synthetic row guarantees a 'running' one is present.
    gp_statuses = {r["status"] for r in runs if r["endpoint"] == "gp_history"}
    assert "running" in gp_statuses
    # 4. The ledger is capped at the 20 most-recently-active groups.
    assert len(runs) <= 20


@pytest.mark.db
def test_coverage_counts_catalogs_not_identifier_rows(db_conn):
    """Audit 2026-11-13: the "at least 2 source identifiers" meter read 100% because it counted
    identifier ROWS, and one SATCAT record alone writes three of them. 1,128 on-orbit payloads
    had no GCAT row at all. both_catalogs asks whether SATCAT and GCAT each hold an identifier,
    while with_2plus_ids keeps its old meaning for the clients that read multi_source_pct.

    The same fixture pins in_orbit: every object type whose latest status is not DECAYED, so
    the cataloged-objects tile can say how much of the catalog is still up. TEMP tables shadow
    the real ones for this connection only and vanish with it."""
    from api.routers.stats import _COVERAGE_SQL

    db_conn.row_factory = dict_row
    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE satellite (satellite_id bigint PRIMARY KEY, object_type text);
            CREATE TEMP TABLE satellite_status_history (
                satellite_id bigint, canonical_status text, observed_at timestamptz);
            CREATE TEMP TABLE satellite_operator (satellite_id bigint, role text, valid_to date);
            CREATE TEMP TABLE satellite_identifier (satellite_id bigint, source text);
            INSERT INTO satellite VALUES
                (1, 'PAYLOAD'), (2, 'PAYLOAD'), (3, 'PAYLOAD'), (4, 'PAYLOAD'),
                (5, 'DEBRIS'), (6, 'ROCKET_BODY');
            INSERT INTO satellite_status_history VALUES
                (1, 'ACTIVE', now()), (2, 'ACTIVE', now()),
                (4, 'ACTIVE', now() - interval '1 year'), (4, 'DECAYED', now()),
                (6, 'DECAYED', now());
            -- 1: both catalogs. 2: SATCAT only, but three rows (norad, cospar, name).
            -- 3: no status record (still on orbit by the rule), one row from each catalog.
            INSERT INTO satellite_identifier VALUES
                (1, 'satcat'), (1, 'satcat'), (1, 'satcat'), (1, 'gcat'), (1, 'gcat'),
                (2, 'satcat'), (2, 'satcat'), (2, 'satcat'),
                (3, 'satcat'), (3, 'gcat'),
                (4, 'satcat'), (4, 'gcat');
        """)
        cur.execute(_COVERAGE_SQL)
        cov = cur.fetchone()
    db_conn.rollback()

    assert cov["total_in_orbit"] == 4  # payloads 1, 2 and 3 and the debris piece 5
    assert cov["total_on_orbit"] == 3
    assert cov["with_2plus_ids"] == 3  # the row count passes the SATCAT-only payload 2
    assert cov["with_both_catalogs"] == 2  # the catalog count does not


class _CannedCursor:
    """Answers _build_stats's three queries in order, for a DB-free check of its keys."""

    def __init__(self):
        self._answers = iter([
            {"satellites": 10, "operators": 3, "identifier_rows": 40, "merge_events": 41,
             "gp_elements": 900},
            {"total_in_orbit": 6, "total_on_orbit": 4, "with_operator": 4, "with_status": 3,
             "with_2plus_ids": 4, "with_both_catalogs": 3},
        ])

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _sql, _params=None):
        pass

    def fetchone(self):
        return next(self._answers)

    def fetchall(self):
        return []


class _CannedDB:
    def cursor(self):
        return self._cursor

    def __init__(self):
        self._cursor = _CannedCursor()


def test_stats_payload_adds_keys_and_keeps_the_old_ones(monkeypatch):
    """The landing page at vibcreates.com reads satellites, gp_elements, coverage.operator_pct
    and the conflict counts through /live/orbital, so new facts arrive as new keys and no key
    changes its meaning."""
    from api.routers import stats

    for name in ("count_status_conflicts", "count_decay_conflicts", "count_stale_owners"):
        monkeypatch.setattr(stats, name, lambda _db: 0)
    body = stats._build_stats(_CannedDB())

    assert body["satellites"] == 10
    assert body["in_orbit_objects"] == 6
    assert body["on_orbit_payloads"] == 4
    assert body["gp_elements"] == 900
    assert body["coverage"] == {
        "operator_pct": 100.0, "status_pct": 75.0, "multi_source_pct": 100.0,
        "both_catalogs_pct": 75.0,
    }
