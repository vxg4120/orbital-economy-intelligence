"""Contract + known-data tests for /api/buses (Bus Benchmarks)."""

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app

_LEADERBOARD_ROW_KEYS = {
    "slug", "name", "fleet_total", "fleet_on_orbit", "fleet_active",
    "decayed_count", "decayed_share_pct", "lifetime_n", "median_lifetime_years",
    "tto_n", "median_days_to_operational", "sk_n", "station_keeping_share_pct",
    "p50_station_keeping_km", "disposal_n", "disposal_compliance_pct",
    "gp_n", "gp_coverage_pct",
}


@pytest.fixture
def client(db_conn):
    return TestClient(app)


@pytest.mark.db
def test_leaderboard_shape_ordering_and_cohort_floor(client):
    r = client.get("/api/buses?limit=10&sort=fleet&min_n=5")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    assert body["group"] == "manufacturer" and body["min_n"] == 5
    rows = body["rows"]
    assert 0 < len(rows) <= 10
    for row in rows:
        assert _LEADERBOARD_ROW_KEYS <= set(row)
        assert row["fleet_total"] >= 5, "min_n floor must hold"
        assert row["fleet_total"] >= row["fleet_on_orbit"] >= row["fleet_active"]
        # Behavior metrics are computed over an observed slice never larger than the fleet.
        assert row["gp_n"] <= row["fleet_total"]
        assert row["sk_n"] <= row["fleet_total"]
    fleets = [row["fleet_total"] for row in rows]
    assert fleets == sorted(fleets, reverse=True)


@pytest.mark.db
def test_leaderboard_bus_group_and_min_n(client):
    r = client.get("/api/buses?group=bus&limit=10&min_n=50")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert rows, "there are bus models with cohorts >= 50"
    for row in rows:
        assert row["fleet_total"] >= 50
        assert "primary_manufacturer" in row


@pytest.mark.db
def test_leaderboard_rejects_bad_params(client):
    assert client.get("/api/buses?sort=bogus").status_code == 422
    assert client.get("/api/buses?group=bogus").status_code == 422
    assert client.get("/api/buses?limit=999").status_code == 422
    assert client.get("/api/buses?min_n=0").status_code == 422


@pytest.mark.db
def test_methodology_is_versioned_and_complete(client):
    r = client.get("/api/buses/methodology")
    assert r.status_code == 200
    m = r.json()
    assert m["version"] and m["updated_at"] and m["doc_url"].startswith("https://")
    assert m["cohort_minimum"] == 5
    metric_keys = {x["key"] for x in m["metrics"]}
    assert {"fleet", "tto", "sk_share", "decayed_share", "compliance", "coverage"} <= metric_keys
    assert m["provenance_guarantee"]
    assert "operator_confirmed" in m["correction_channel"]
    assert m["limitations"], "known limitations must be stated"
    # Site-wide copy rule: no em dashes anywhere in the methodology payload.
    assert "\u2014" not in r.text


@pytest.mark.db
def test_detail_of_top_manufacturer(client):
    top = client.get("/api/buses?limit=1&sort=fleet").json()["rows"][0]
    r = client.get(f"/api/buses/{top['slug']}")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "manufacturer"
    assert body["benchmark"]["slug"] == top["slug"]
    assert body["constituents"], "top manufacturer must expose constituent bus models"
    assert body["orgs"], "manufacturer detail must expose constituent GCAT orgs"
    assert 0 < len(body["satellites_sample"]) <= 20
    for sat in body["satellites_sample"]:
        assert {"satellite_id", "canonical_name", "source", "source_key",
                "ingest_run_id"} <= set(sat)
    prov = body["provenance"]
    assert prov["source"] == "gcat" and prov["methodology_version"]
    for key in ("gp_behavior", "station_keeping", "time_to_operational", "disposal"):
        cov = prov["metric_coverage"][key]
        assert 0 <= cov["n"] <= max(cov["of"], body["benchmark"]["fleet_total"])
    assert "vibhavgupta2@gmail.com" in body["correction_channel"]


@pytest.mark.db
def test_detail_of_top_bus_model(client):
    top = client.get("/api/buses?group=bus&limit=1&sort=fleet").json()["rows"][0]
    r = client.get(f"/api/buses/{top['slug']}?kind=bus")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "bus"
    assert body["constituents"], "bus detail must expose constituent manufacturers"


@pytest.mark.db
def test_detail_unknown_slug_is_404(client):
    assert client.get("/api/buses/zz-no-such-slug").status_code == 404


@pytest.mark.db
def test_provenance_receipts_reconcile_with_headline(client):
    top = client.get("/api/buses?limit=1&sort=fleet").json()["rows"][0]
    r = client.get(f"/api/buses/{top['slug']}/provenance?metric=fleet&limit=5")
    assert r.status_code == 200
    body = r.json()
    # The receipts total must equal the headline number it backs: full traceability.
    assert body["total"] == top["fleet_total"]
    assert body["methodology_version"]
    for row in body["rows"]:
        assert row["source"] == "gcat"
        assert row["source_key"], "every receipt row carries its GCAT source row key"
        assert row["ingest_run_id"] is not None

    r = client.get(f"/api/buses/{top['slug']}/provenance?metric=station_keeping&limit=5")
    assert r.status_code == 200
    assert r.json()["total"] == top["sk_n"]

    assert client.get(f"/api/buses/{top['slug']}/provenance?metric=bogus").status_code == 422


@pytest.mark.db
def test_history_returns_snapshots(client):
    top = client.get("/api/buses?limit=1&sort=fleet").json()["rows"][0]
    r = client.get(f"/api/buses/history/{top['slug']}")
    if r.status_code == 404:
        pytest.skip("no snapshot captured yet in this build")
    body = r.json()
    assert body["snapshots"]
    for snap in body["snapshots"]:
        assert snap["methodology_version"]
        assert snap["metrics"]["fleet_total"] >= 1


@pytest.mark.db
def test_view_has_manufacturer_cohorts_of_5_plus(db_conn):
    """The benchmark view itself (not just the API) yields real cohorts at the default floor."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), max(fleet_total) FROM v_bus_benchmarks_manufacturer "
            "WHERE fleet_total >= 5"
        )
        n, max_fleet = cur.fetchone()
    assert n > 0, "expected manufacturers with cohort >= 5"
    assert max_fleet >= 5
