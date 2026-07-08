"""Contract + known-data tests for GET /api/stats (Task F1).

DB-marked: runs a FastAPI TestClient against the live dev DB (read-only, seeds nothing). Asserts
the exact field names from the spec's API contract plus spot checks on real data.
"""

import warnings

import pytest

# TestClient's transport import emits a StarletteDeprecationWarning at import time; suppress it
# here so the module imports cleanly under `pytest -W error` (it is not our warning to fix).
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

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
