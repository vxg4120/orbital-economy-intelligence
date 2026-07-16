"""Contract + sanity tests for the /api/twoskies/* Two Skies bridge (satellites x exoplanets).

Read-only across both databases (`oei` for the satellite catalog, `exo` for the targets). Tests that
need the second database are gated on its reachability so the suite skips cleanly where only one DB
is up. The /passes sanity test asserts the geometry contract (every returned separation is within the
threshold; a below-horizon target yields an empty, crash-free result) rather than an exact count,
since propagated positions drift as the element sets age.
"""

import os
import warnings

import psycopg
import pytest

from api.deps import EXO_DATABASE_URL_DEFAULT

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app


def _exo_reachable() -> bool:
    url = os.environ.get("EXO_DATABASE_URL", EXO_DATABASE_URL_DEFAULT)
    try:
        psycopg.connect(url).close()
        return True
    except psycopg.OperationalError:
        return False


exo_required = pytest.mark.skipif(not _exo_reachable(), reason="exo database not reachable")

# A UTC instant + target/site where geometry is fully determined by Earth rotation (independent of
# element-set freshness): AU Mic culminates high over Cerro Paranal; TRAPPIST-1 is below the horizon
# at Kitt Peak. These stay stable regardless of catalog age.
_AU_MIC = {"ra": 311.291137, "dec": -31.34245}
_TRAPPIST1 = {"ra": 346.6263919, "dec": -5.0434618}
_WHEN = "2026-07-16T05:00:00Z"

_PASS_KEYS = {"norad", "name", "operator", "alt_km", "closest_sep_deg", "alt_deg", "time_utc"}


@pytest.fixture
def client(db_conn):
    return TestClient(app)


# --------------------------------------------------------------------------- /targets
@exo_required
@pytest.mark.db
def test_targets_shape_and_conflict_flag(client):
    r = client.get("/api/twoskies/targets")
    assert r.status_code == 200
    body = r.json()
    assert 30 <= len(body["targets"]) <= 120
    assert body["n_famous"] > 0 and body["n_conflict"] > 0

    for t in body["targets"]:
        assert {"candidate", "host", "ra_deg", "dec_deg", "disposition", "has_conflict"} <= set(t)
        assert 0.0 <= t["ra_deg"] <= 360.0
        assert -90.0 <= t["dec_deg"] <= 90.0
        assert isinstance(t["has_conflict"], bool)

    # A famous host with coordinates is present, and at least one conflict-flagged target exists.
    hosts = {t["host"] for t in body["targets"]}
    assert "TRAPPIST-1" in hosts
    conflicted = [t for t in body["targets"] if t["has_conflict"]]
    assert conflicted, "expected at least one conflict-flagged target"
    assert all(t["ra_deg"] is not None and t["dec_deg"] is not None for t in conflicted)


# ---------------------------------------------------------------- /congestion-astronomy
@pytest.mark.db
def test_congestion_astronomy_shape(client):
    r = client.get("/api/twoskies/congestion-astronomy")
    assert r.status_code == 200
    body = r.json()
    assert body["catalog_objects"] > 1000
    assert body["tracked_with_elements"] > 1000
    assert body["leo_objects"] > 0
    assert body["bins"] and body["shells"]
    assert body["top_operators"][0]["payloads"] > 0
    # bins carry the heatmap triple; caveats are present (the honest framing is mandatory).
    for b in body["bins"]:
        assert set(b) == {"alt_bin_km", "inc_bin_deg", "object_count"}
    assert len(body["caveats"]) >= 2


# --------------------------------------------------------------------------- /passes
@pytest.mark.db
def test_passes_geometry_contract(client):
    r = client.get("/api/twoskies/passes", params={**_AU_MIC, "datetime": _WHEN,
                                                    "site": "paranal", "window_min": 60, "sep_deg": 5})
    assert r.status_code == 200
    body = r.json()

    # AU Mic is up over Paranal at this instant (pure Earth-rotation geometry -- stable).
    assert body["target_visible"] is True
    assert body["target_max_alt_deg"] > 20.0
    assert body["n_considered"] > 100
    assert body["n_found"] >= 0
    assert isinstance(body["passes"], list)

    # The geometry contract: every returned pass is within the threshold and above the horizon.
    for p in body["passes"]:
        assert set(p) == _PASS_KEYS
        assert p["closest_sep_deg"] <= 5.0
        assert p["alt_deg"] >= 0.0
        assert p["time_utc"].endswith("Z")
    # sorted ascending by closest separation.
    seps = [p["closest_sep_deg"] for p in body["passes"]]
    assert seps == sorted(seps)


@pytest.mark.db
def test_passes_below_horizon_target_is_empty(client):
    # TRAPPIST-1 is below the observability gate at Kitt Peak at this instant -> empty, no crash.
    r = client.get("/api/twoskies/passes", params={**_TRAPPIST1, "datetime": _WHEN,
                                                    "site": "kitt_peak"})
    assert r.status_code == 200
    body = r.json()
    assert body["target_visible"] is False
    assert body["n_found"] == 0
    assert body["passes"] == []


@pytest.mark.db
def test_passes_default_datetime_runs(client):
    # No datetime => "now"; must run cleanly whatever the current sky looks like.
    r = client.get("/api/twoskies/passes", params={**_AU_MIC, "site": "generic_north"})
    assert r.status_code == 200
    body = r.json()
    assert body["elapsed_ms"] > 0
    assert all(p["closest_sep_deg"] <= body["sep_deg"] for p in body["passes"])


@pytest.mark.db
def test_passes_custom_latlon(client):
    r = client.get("/api/twoskies/passes", params={**_AU_MIC, "datetime": _WHEN,
                                                    "lat": -24.6, "lon": -70.4, "elev_km": 2.6})
    assert r.status_code == 200
    assert r.json()["site"]["key"] is None  # custom site, no preset key


@pytest.mark.db
def test_passes_unknown_site_is_422(client):
    r = client.get("/api/twoskies/passes", params={**_AU_MIC, "site": "atlantis"})
    assert r.status_code == 422
