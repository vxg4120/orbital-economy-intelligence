"""Contract + internal-consistency tests for GET /api/audit/summary (the Overview audit strip).

DB-marked: a FastAPI TestClient runs against the live dev DB (read-only), computing the three
auditor headline numbers live. Assertions are on shape + internal consistency, not exact counts
(which move with each ingest), so the tests stay honest as the data grows.
"""

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client(db_conn):  # db_conn: skips the whole module when the dev DB is unreachable
    return TestClient(app)


@pytest.mark.db
def test_audit_summary_shape(client):
    r = client.get("/api/audit/summary")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"kuiper_milestone", "lingering_leaderboard", "active_but_decaying"}


@pytest.mark.db
def test_kuiper_milestone_internally_consistent(client):
    km = client.get("/api/audit/summary").json()["kuiper_milestone"]
    assert {"at_shell", "raising", "deorbited", "deployed_total", "deployed_last_30d",
            "required", "deadline"} <= set(km)
    assert km["required"] == 1618
    assert km["deadline"] == "2026-07-30"
    for key in ("at_shell", "raising", "deorbited", "deployed_total", "deployed_last_30d"):
        assert isinstance(km[key], int) and km[key] >= 0
    # The three orbit buckets partition a subset of the fleet -- never more than were deployed.
    assert km["at_shell"] + km["raising"] + km["deorbited"] <= km["deployed_total"]
    # The real build is nowhere near the 1,618 obligation -- that gap IS the thesis.
    assert km["deployed_total"] < km["required"]


@pytest.mark.db
def test_lingering_leaderboard_nonempty_and_shaped(client):
    rows = client.get("/api/audit/summary").json()["lingering_leaderboard"]
    assert rows, "the lingering leaderboard should have at least one benchmark operator"
    assert len(rows) <= 6
    prev = None
    for row in rows:
        assert {"operator", "count", "avg_alt_km"} <= set(row)
        assert isinstance(row["operator"], str) and row["operator"]
        assert isinstance(row["count"], int) and row["count"] > 0
        assert isinstance(row["avg_alt_km"], (int, float)) and row["avg_alt_km"] > 500
        if prev is not None:
            assert row["count"] <= prev, "leaderboard must be ordered by count descending"
        prev = row["count"]


@pytest.mark.db
def test_active_but_decaying_positive(client):
    n = client.get("/api/audit/summary").json()["active_but_decaying"]
    assert isinstance(n, int)
    assert n > 0, "the catalog-active / physics-decaying count is the headline number"
