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
def test_stats_shape_and_spot_checks(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()

    for key in ("satellites", "on_orbit_payloads", "operators", "identifier_rows",
                "merge_events", "gp_elements", "coverage", "conflicts", "ingest_runs"):
        assert key in body, f"missing top-level key {key}"

    # Spot check: the real build has > 60k satellites.
    assert isinstance(body["satellites"], int)
    assert body["satellites"] > 60000

    cov = body["coverage"]
    assert set(cov) == {"operator_pct", "status_pct", "multi_source_pct"}
    for name, pct in cov.items():
        assert isinstance(pct, float), f"{name} must be a float"
        assert 0.0 <= pct <= 100.0, f"{name} out of range: {pct}"

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
