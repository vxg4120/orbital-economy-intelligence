"""Contract + known-data tests for /api/operators and /api/operators/{id} (Task F1)."""

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app

_LEAGUE_ROW_KEYS = {"operator_id", "canonical_name", "country", "operator_class", "parent_name",
                    "fleet_total", "fleet_on_orbit", "fleet_active"}


@pytest.fixture
def client(db_conn):
    return TestClient(app)


@pytest.mark.db
def test_league_table_shape_and_ordering(client):
    r = client.get("/api/operators?limit=10&sort=fleet")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    rows = body["rows"]
    assert 0 < len(rows) <= 10
    for row in rows:
        assert _LEAGUE_ROW_KEYS <= set(row)
        assert row["fleet_total"] >= row["fleet_on_orbit"] >= row["fleet_active"]
    # sort=fleet -> descending fleet_total.
    fleets = [row["fleet_total"] for row in rows]
    assert fleets == sorted(fleets, reverse=True)


@pytest.mark.db
def test_sort_by_name_and_bad_sort(client):
    assert client.get("/api/operators?limit=5&sort=name").status_code == 200
    assert client.get("/api/operators?sort=bogus").status_code == 422
    assert client.get("/api/operators?limit=201").status_code == 422


@pytest.mark.db
def test_detail_of_top_operator(client):
    top = client.get("/api/operators?limit=1&sort=fleet").json()["rows"][0]
    r = client.get(f"/api/operators/{top['operator_id']}")
    assert r.status_code == 200
    body = r.json()

    for key in ("operator", "parents", "children", "fleet_by_status", "fleet_by_regime",
                "acquisitions", "top_satellites"):
        assert key in body

    op = body["operator"]
    assert op["operator_id"] == top["operator_id"]
    assert op["fleet_total"] > 0
    assert body["fleet_by_status"], "top operator must have a non-empty status breakdown"
    assert body["fleet_by_regime"], "top operator must have a non-empty regime breakdown"
    assert body["top_satellites"], "top operator must list fleet members"
    assert len(body["top_satellites"]) <= 20
    for key in ("parents", "children", "acquisitions", "top_satellites"):
        assert isinstance(body[key], list)


@pytest.mark.db
def test_detail_exposes_mso_hierarchy(client):
    # Find a league operator that has a current parent, then verify the detail wires the edge.
    parented = None
    for offset in range(0, 1400, 200):
        rows = client.get(f"/api/operators?limit=200&offset={offset}").json()["rows"]
        parented = next((row for row in rows if row["parent_name"]), None)
        if parented:
            break
    if parented is None:
        pytest.skip("no operator with a current parent in this build")

    body = client.get(f"/api/operators/{parented['operator_id']}").json()
    assert body["parents"], "operator with a parent_name must expose it in detail.parents"
    parent_names = {p["canonical_name"] for p in body["parents"]}
    assert parented["parent_name"] in parent_names
    for p in body["parents"]:
        assert {"operator_id", "canonical_name", "relationship", "valid_from", "valid_to"} <= set(p)


@pytest.mark.db
def test_detail_unknown_id_is_404(client):
    assert client.get("/api/operators/999999999").status_code == 404


@pytest.mark.db
def test_total_is_stable_past_last_row(client):
    # Regression: an offset beyond the last operator must still report the true total (not 0) and
    # return an empty page -- the windowed count(*) OVER() used to collapse once the page emptied.
    true_total = client.get("/api/operators?limit=1&offset=0").json()["total"]
    assert true_total > 0
    beyond = client.get(f"/api/operators?limit=50&offset={true_total + 500}").json()
    assert beyond["rows"] == []
    assert beyond["total"] == true_total
