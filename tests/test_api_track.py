"""Contract + known-data tests for GET /api/satellites/{id}/track (the LifeTrack series).

DB-marked: a FastAPI TestClient runs against the live dev DB (read-only). The ISS (NORAD 25544) is
the stable known object with daily orbit history; a NULL-norad analyst object and a nonexistent id
exercise the graceful-empty and 404 paths.
"""

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app

ISS_NORAD = 25544

_POINT_KEYS = {"day", "sma_km", "perigee_km", "apogee_km", "elsets"}
_TRACK_KEYS = {"norad_id", "span_days", "points"}


@pytest.fixture
def client(db_conn):  # db_conn: skips the whole module when the dev DB is unreachable
    return TestClient(app)


@pytest.mark.db
def test_track_iss_returns_points(client):
    sat_id = client.get(f"/api/satellites/search?q={ISS_NORAD}").json()["results"][0]["satellite_id"]
    r = client.get(f"/api/satellites/{sat_id}/track")
    assert r.status_code == 200
    body = r.json()
    assert _TRACK_KEYS <= set(body)
    assert body["norad_id"] == ISS_NORAD
    assert body["points"], "ISS must have a non-empty daily orbit series"
    assert len(body["points"]) <= 400, "series must be capped at 400 points"
    assert isinstance(body["span_days"], int) and body["span_days"] >= 0
    for p in body["points"]:
        assert _POINT_KEYS <= set(p)
        assert isinstance(p["day"], str) and len(p["day"]) == 10  # YYYY-MM-DD
        assert isinstance(p["elsets"], int)
    # A real LEO orbit: sma (radius) sits well above the perigee/apogee altitudes.
    p0 = body["points"][0]
    assert p0["sma_km"] is not None and p0["sma_km"] > 6500


@pytest.mark.db
def test_track_unknown_id_is_404(client):
    assert client.get("/api/satellites/999999999/track").status_code == 404


@pytest.mark.db
def test_track_null_norad_is_empty(client, db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT satellite_id FROM satellite WHERE norad_id IS NULL LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("no NULL-norad satellite in this build")
    body = client.get(f"/api/satellites/{row[0]}/track").json()
    assert body["norad_id"] is None
    assert body["points"] == []
    assert body["span_days"] == 0
