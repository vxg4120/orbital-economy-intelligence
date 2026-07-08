"""Contract + known-data tests for the /api/conflicts/* endpoints (Task F1).

These endpoints mirror quality/report.py sections 1-3; the totals here should track that report.
"""

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client(db_conn):
    return TestClient(app)


@pytest.mark.db
def test_status_conflicts(client):
    r = client.get("/api/conflicts/status?limit=50")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 30  # report.py section 1 count on the real build
    assert 0 < len(body["rows"]) <= 50
    for row in body["rows"]:
        assert set(row) == {"satellite_id", "norad_id", "canonical_name", "satcat_status",
                            "gcat_status"}
        assert row["satcat_status"] != row["gcat_status"]


@pytest.mark.db
def test_decay_conflicts_show_dated_provenance(client):
    r = client.get("/api/conflicts/decay?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    assert 0 < len(body["rows"]) <= 5
    for row in body["rows"]:
        assert set(row) == {"satellite_id", "norad_id", "canonical_name", "sources_and_dates"}
        # sources_and_dates lists at least two "source: value" claims joined by ';'.
        assert ";" in row["sources_and_dates"] and ":" in row["sources_and_dates"]


@pytest.mark.db
def test_stale_owners_expose_ma_lag(client):
    r = client.get("/api/conflicts/stale-owners?limit=50")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    for row in body["rows"]:
        assert set(row) == {"satellite_id", "norad_id", "canonical_name", "catalog_owner",
                            "resolved_operator", "acquired_by", "acquisition_date"}
        # The resolved (child) operator differs from the acquiring (parent) operator.
        assert row["resolved_operator"] != row["acquired_by"]


@pytest.mark.db
def test_pagination_is_bounded_and_offsets(client):
    # limit above the 200 cap is rejected.
    assert client.get("/api/conflicts/status?limit=201").status_code == 422

    first = client.get("/api/conflicts/status?limit=1&offset=0").json()
    second = client.get("/api/conflicts/status?limit=1&offset=1").json()
    assert first["total"] == second["total"]
    assert len(first["rows"]) == 1
    # Same deterministic ordering -> distinct rows across offsets.
    assert first["rows"][0]["satellite_id"] != second["rows"][0]["satellite_id"]
