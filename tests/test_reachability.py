"""Tests for the RF layer: geometry, loaders, join build, and the reachability API.

The geometry test is the load-bearing one. It validates the entire propagation chain (element
construction, SGP4, TEME to ECEF rotation, topocentric transform) against physics that cannot
be wrong: a satellite in a geostationary-period equatorial orbit positioned over a known
longitude must appear near zenith at ~35,786 km to an observer directly beneath it, and below
the horizon to an observer on the opposite side of the planet. No fixture data, no network.
"""

import datetime as dt
import math

import pytest

from api.routers import reachability as rch
from ingest import fcc_ssal, satnogs_db


def _gso_row_over_longitude(lon_deg: float, epoch: dt.datetime) -> dict:
    """Mean elements for a circular, equatorial, geosynchronous-period orbit whose subpoint at
    `epoch` is `lon_deg`. In TEME at epoch, the satellite's right ascension equals GMST + lon;
    with inclination ~0 we place it via RAAN and set argp = mean anomaly = 0."""
    jd, fr = rch._epoch_to_sgp4(epoch)
    import numpy as np

    gmst_deg = math.degrees(rch._gmst_rad(np.array([jd + fr]))[0])
    return {
        "norad_id": 99999,
        "epoch": epoch,
        "mean_motion": 1.0027379,  # rev/day: geosynchronous period
        "eccentricity": 0.0001,
        "inclination": 0.01,
        "ra_of_asc_node": (gmst_deg + lon_deg) % 360.0,
        "arg_of_pericenter": 0.0,
        "mean_anomaly": 0.0,
        "bstar": 0.0,
    }


def test_geometry_chain_places_gso_at_zenith():
    epoch = dt.datetime(2026, 7, 29, 12, 0, 0, tzinfo=dt.timezone.utc)
    lon = -100.0
    sat = rch._build_satrec(_gso_row_over_longitude(lon, epoch))
    import numpy as np
    from sgp4.api import SatrecArray

    jd, fr = rch._epoch_to_sgp4(epoch)
    err, r_teme, _ = SatrecArray([sat]).sgp4(np.array([jd]), np.array([fr]))
    assert err[0][0] == 0
    # Observer on the equator directly beneath: near-zenith, near-GEO range.
    az, el, rng = rch._az_el_range(r_teme[0], np.array([jd + fr]), 0.0, lon, 0.0)
    assert el[0] > 85.0, f"expected near-zenith, got elevation {el[0]:.2f}"
    assert abs(rng[0] - 35786) < 600, f"expected ~GEO range, got {rng[0]:.0f} km"
    # Observer on the opposite side of the planet: far below the horizon.
    antipode = ((lon + 180.0) + 180.0) % 360.0 - 180.0
    az2, el2, rng2 = rch._az_el_range(r_teme[0], np.array([jd + fr]), 0.0, antipode, 0.0)
    assert el2[0] < -60.0
    # An observer 45 degrees of longitude away still sees it, but low.
    az3, el3, _ = rch._az_el_range(r_teme[0], np.array([jd + fr]), 0.0, lon + 45, 0.0)
    assert 30.0 < el3[0] < 45.0


def test_gso_elevation_falls_with_observer_latitude():
    """Monotonic physics check: the same GEO bird sits lower in the sky the further north you
    stand, and disappears near the pole."""
    epoch = dt.datetime(2026, 7, 29, 0, 0, 0, tzinfo=dt.timezone.utc)
    sat = rch._build_satrec(_gso_row_over_longitude(0.0, epoch))
    import numpy as np
    from sgp4.api import SatrecArray

    jd, fr = rch._epoch_to_sgp4(epoch)
    _, r_teme, _ = SatrecArray([sat]).sgp4(np.array([jd]), np.array([fr]))
    els = []
    for lat in (0.0, 30.0, 60.0, 85.0):
        _, el, _ = rch._az_el_range(r_teme[0], np.array([jd + fr]), lat, 0.0, 0.0)
        els.append(float(el[0]))
    assert els[0] > els[1] > els[2] > els[3]
    assert els[3] < 0.0, "a GEO satellite is below the horizon from 85 degrees north"


def test_satnogs_parse_rows_coerces_and_ignores_unknown_keys():
    pages = [[
        {
            "uuid": "abc", "norad_cat_id": 25544, "sat_id": "XSKZ", "description": "Mode V APRS",
            "type": "Transceiver", "status": "active", "alive": True,
            "downlink_low": 145825000, "uplink_low": 145825000, "mode": "AFSK", "baud": 1200.0,
            "service": "Amateur", "citation": "https://example.org", "iaru_coordination": "N/A",
            "frequency_violation": False, "unconfirmed": False,
            "updated": "2024-01-01T00:00:00Z",
            "some_future_api_field": "ignored",
        },
        {"uuid": "def", "norad_cat_id": None, "updated": "not a date"},
    ]]
    rows = satnogs_db.parse_rows(pages)
    assert len(rows) == 2
    assert rows[0]["norad_cat_id"] == 25544
    assert rows[0]["updated"].year == 2024
    assert "some_future_api_field" not in rows[0]
    assert rows[1]["updated"] is None  # unparseable degrades to NULL, never raises


def test_ssal_parse_rows_finds_header_and_skips_section_rows():
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["FCC Approved Space Station List"])  # title junk above the header
    ws.append([])
    ws.append(["Orbital Location", "Satellite Name", "Call Sign", "Licensee or Grantee",
               "Administration", "Service", "Frequency Range", "Date In-orbit and Operating",
               "Grant Type", "Notes"])
    ws.append(["103 W.L.", "SES-3", "S2811", "SES Americom", "USA", "FSS",
               "3700-4200 MHz (s-E)", "2011", "License", None])
    ws.append(["NGSO", None, None, None, None, None, None, None, None, None])  # section row
    ws.append([None, "SPACEX GEN-2", "S3069", "Space Exploration Holdings", "USA", "FSS",
               "10.7-12.7 GHz (s-E)", "2023", "License", "NGSO system"])
    buf = io.BytesIO()
    wb.save(buf)
    rows = fcc_ssal.parse_rows(buf.getvalue())
    assert len(rows) == 2
    assert rows[0]["call_sign"] == "S2811"
    assert rows[0]["frequency_range"] == "3700-4200 MHz (s-E)"
    assert rows[1]["satellite_name"] == "SPACEX GEN-2"
    assert rows[1]["licensee"] == "Space Exploration Holdings"


def test_ssal_parse_rows_raises_without_header():
    import io

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["nothing", "useful", "here"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError):
        fcc_ssal.parse_rows(buf.getvalue())


@pytest.mark.db
def test_reachability_endpoint_contract(db_conn):
    """Contract over live data: parameters validate, geometry fields are sane, RF layers are
    lists, and the SatNOGS attribution is always present (CC BY-SA requires it)."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    assert client.get("/api/reachability?lat=91&lon=0").status_code == 422
    r = client.get("/api/reachability?lat=34.05&lon=-118.24&min_elev=5")
    assert r.status_code == 200
    body = r.json()
    assert "attribution" in body and "SatNOGS" in body["attribution"]
    if not body["visible"]:
        pytest.skip("no RF-carrying satellite above LA right now with fresh elements")
    top = body["visible"][0]
    assert 5.0 <= top["elevation_deg"] <= 90.0
    assert 0.0 <= top["azimuth_deg"] < 360.0
    assert 200.0 < top["range_km"] < 60000.0
    assert isinstance(top["satnogs"], list) and isinstance(top["fcc"], list)
    assert top["satnogs"] or top["fcc"], "rf=only must mean at least one RF layer is non-empty"


@pytest.mark.db
def test_passes_endpoint_contract(db_conn):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT norad_id FROM gp_elements WHERE epoch >= now() - interval '7 days' "
            "AND norad_id IS NOT NULL ORDER BY epoch DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        pytest.skip("no fresh GP elements in this database")
    r = client.get(f"/api/reachability/passes?lat=34.05&lon=-118.24&norad={row[0]}&hours=24")
    assert r.status_code == 200
    body = r.json()
    assert body["norad_id"] == row[0]
    for p in body["passes"]:
        assert p["rise"] <= p["peak"] <= p["set"]
        assert p["peak_elevation_deg"] >= 10.0
        assert p["duration_s"] >= 30
    assert client.get("/api/reachability/passes?lat=0&lon=0&norad=999999999").status_code == 404
