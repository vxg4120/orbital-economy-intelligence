"""Reachability: which satellites are above your horizon, and what can you receive.

GET /api/reachability?lat=&lon=            visible-now list with the layered RF picture
GET /api/reachability/passes?lat=&lon=&norad=   pass windows for one satellite

The answer fuses three layers that are deliberately kept distinct in the payload, because they
make different claims: the catalog says the object exists and where it is (our GP elements,
propagated with SGP4); SatNOGS DB says what amateurs actually receive (community-curated,
CC BY-SA 4.0, cited per record); satellite_fcc_authorization says what the regulator authorized
(with the match tier that joined it). An authorization is not a transmission and a crowdsourced
"active" is not an authorization; serving them as separate fields is the honesty.

Propagation notes: TEME positions from python-sgp4 (vectorized SatrecArray), rotated to ECEF by
GMST (IAU-82 form), topocentric az/el against a WGS84 observer. With nightly-refreshed GP the
pass timing error is dominated by element age at roughly 1-3 km along-track per day, which at
LEO ground speed is a fraction of a second per day of staleness: more than adequate for antenna
pointing. Elements older than MAX_ELEMENT_AGE_DAYS are excluded rather than served stale.
"""

from __future__ import annotations

import datetime as dt
import math
import threading
import time

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sgp4.api import WGS72, Satrec, SatrecArray, jday

from api.deps import get_db

router = APIRouter(prefix="/reachability", tags=["reachability"])

MAX_ELEMENT_AGE_DAYS = 14
_CACHE_TTL_S = 600
_DEG = math.pi / 180.0

SATNOGS_ATTRIBUTION = (
    "Transmitter data from SatNOGS DB (https://db.satnogs.org), CC BY-SA 4.0."
)

# ---------------------------------------------------------------------------------------------
# candidate set: latest fresh GP element per satellite that has any RF story to tell
# ---------------------------------------------------------------------------------------------

_CANDIDATES_SQL = """
WITH latest_gp AS (
    SELECT DISTINCT ON (norad_id) norad_id, epoch, mean_motion, eccentricity, inclination,
           ra_of_asc_node, arg_of_pericenter, mean_anomaly, bstar
    FROM gp_elements
    WHERE epoch >= now() - %(age_days)s * interval '1 day'
    ORDER BY norad_id, epoch DESC
),
tx AS (
    SELECT norad_cat_id, count(*) AS n
    FROM raw_satnogs_transmitters
    WHERE ingest_run_id = (
        SELECT max(r.ingest_run_id) FROM raw_satnogs_transmitters r
        JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'
    )
    AND status = 'active'
    GROUP BY norad_cat_id
),
fcc AS (
    SELECT s.norad_id, count(*) AS n
    FROM satellite_fcc_authorization a
    JOIN satellite s ON s.satellite_id = a.satellite_id
    WHERE s.norad_id IS NOT NULL
    GROUP BY s.norad_id
)
SELECT g.norad_id, g.epoch, g.mean_motion, g.eccentricity, g.inclination,
       g.ra_of_asc_node, g.arg_of_pericenter, g.mean_anomaly, g.bstar,
       s.satellite_id, s.canonical_name,
       COALESCE(tx.n, 0) AS satnogs_active_transmitters,
       COALESCE(fcc.n, 0) AS fcc_authorizations
FROM latest_gp g
JOIN satellite s ON s.norad_id = g.norad_id
LEFT JOIN tx ON tx.norad_cat_id = g.norad_id
LEFT JOIN fcc ON fcc.norad_id = g.norad_id
WHERE (NOT %(rf_only)s) OR tx.n > 0 OR fcc.n > 0
"""


def _epoch_to_sgp4(epoch: dt.datetime) -> tuple[float, float]:
    """(jd, fr) for sgp4, and the same pair serves as the element epoch reference."""
    e = epoch.astimezone(dt.timezone.utc)
    jd, fr = jday(e.year, e.month, e.day, e.hour, e.minute, e.second + e.microsecond / 1e6)
    return jd, fr


def _build_satrec(row: dict) -> Satrec:
    """One Satrec from a GP row. Units per sgp4init: radians, radians/minute, epoch in days
    since 1949-12-31 00:00 UT."""
    jd, fr = _epoch_to_sgp4(row["epoch"])
    sat = Satrec()
    sat.sgp4init(
        WGS72,
        "i",
        int(row["norad_id"]) % 100000,
        (jd + fr) - 2433281.5,
        float(row["bstar"] or 0.0),
        0.0,
        0.0,
        float(row["eccentricity"]),
        float(row["arg_of_pericenter"]) * _DEG,
        float(row["inclination"]) * _DEG,
        float(row["mean_anomaly"]) * _DEG,
        float(row["mean_motion"]) * 2.0 * math.pi / 1440.0,
        float(row["ra_of_asc_node"]) * _DEG,
    )
    return sat


# ---------------------------------------------------------------------------------------------
# geometry: TEME -> ECEF -> topocentric az/el for a WGS84 observer
# ---------------------------------------------------------------------------------------------

_WGS84_A = 6378.137  # km
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)


def _gmst_rad(jd_ut1: np.ndarray) -> np.ndarray:
    """Greenwich mean sidereal time, IAU-82 linear form: plenty for pointing accuracy."""
    d = jd_ut1 - 2451545.0
    gmst_deg = 280.46061837 + 360.98564736629 * d
    return np.remainder(gmst_deg, 360.0) * _DEG


def _observer_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> np.ndarray:
    lat = lat_deg * _DEG
    lon = lon_deg * _DEG
    alt_km = alt_m / 1000.0
    n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * math.sin(lat) ** 2)
    return np.array(
        [
            (n + alt_km) * math.cos(lat) * math.cos(lon),
            (n + alt_km) * math.cos(lat) * math.sin(lon),
            (n * (1.0 - _WGS84_E2) + alt_km) * math.sin(lat),
        ]
    )


def _az_el_range(r_teme: np.ndarray, jd: np.ndarray, lat_deg: float, lon_deg: float,
                 alt_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized az/el/range. r_teme shape (n, 3) km at times jd shape (n,)."""
    theta = _gmst_rad(jd)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    # ECEF = R3(gmst) . TEME
    x = cos_t * r_teme[..., 0] + sin_t * r_teme[..., 1]
    y = -sin_t * r_teme[..., 0] + cos_t * r_teme[..., 1]
    z = r_teme[..., 2]
    obs = _observer_ecef(lat_deg, lon_deg, alt_m)
    dx, dy, dz = x - obs[0], y - obs[1], z - obs[2]
    lat = lat_deg * _DEG
    lon = lon_deg * _DEG
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)
    south = sin_lat * cos_lon * dx + sin_lat * sin_lon * dy - cos_lat * dz
    east = -sin_lon * dx + cos_lon * dy
    up = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
    rng = np.sqrt(dx * dx + dy * dy + dz * dz)
    el = np.degrees(np.arcsin(np.clip(up / rng, -1.0, 1.0)))
    az = np.degrees(np.arctan2(east, -south)) % 360.0
    return az, el, rng


# ---------------------------------------------------------------------------------------------
# candidate cache: Satrecs are rebuilt at most every _CACHE_TTL_S per rf-filter mode
# ---------------------------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict[bool, tuple[float, list[dict], SatrecArray]] = {}


def _candidates(db, rf_only: bool) -> tuple[list[dict], SatrecArray]:
    with _cache_lock:
        hit = _cache.get(rf_only)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
            return hit[1], hit[2]
    with db.cursor() as cur:
        cur.execute(
            _CANDIDATES_SQL,
            {"age_days": MAX_ELEMENT_AGE_DAYS, "rf_only": rf_only},
        )
        rows = cur.fetchall()  # dict rows via api.deps.get_db
    sats = SatrecArray([_build_satrec(r) for r in rows]) if rows else None
    with _cache_lock:
        _cache[rf_only] = (time.monotonic(), rows, sats)
    return rows, sats


def _rf_payload(db, norad_ids: list[int]) -> dict[int, dict]:
    """SatNOGS transmitters and FCC authorizations for a set of norad ids, keyed by id."""
    out: dict[int, dict] = {n: {"transmitters": [], "fcc_authorizations": []} for n in norad_ids}
    if not norad_ids:
        return out
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT norad_cat_id, description, type, downlink_low, downlink_high, uplink_low,
                   mode, baud, service, citation, unconfirmed
            FROM raw_satnogs_transmitters
            WHERE ingest_run_id = (
                SELECT max(r.ingest_run_id) FROM raw_satnogs_transmitters r
                JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok')
              AND status = 'active' AND norad_cat_id = ANY(%(ids)s)
            ORDER BY downlink_low NULLS LAST
            """,
            {"ids": norad_ids},
        )
        for row in cur.fetchall():
            out[row["norad_cat_id"]]["transmitters"].append(
                {k: v for k, v in row.items() if k != "norad_cat_id"}
            )
        cur.execute(
            """
            SELECT s.norad_id, a.call_sign, a.licensee, a.service, a.frequency_range,
                   a.grant_type, a.match_tier
            FROM satellite_fcc_authorization a
            JOIN satellite s ON s.satellite_id = a.satellite_id
            WHERE s.norad_id = ANY(%(ids)s)
            ORDER BY a.call_sign, a.frequency_range
            """,
            {"ids": norad_ids},
        )
        for row in cur.fetchall():
            out[row["norad_id"]]["fcc_authorizations"].append(
                {k: v for k, v in row.items() if k != "norad_id"}
            )
    return out


@router.get("")
def visible_now(
    db=Depends(get_db),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    alt_m: float = Query(0.0, ge=-500, le=9000),
    min_elev: float = Query(10.0, ge=0, le=90),
    rf: str = Query("only", pattern="^(only|all)$"),
    limit: int = Query(100, ge=1, le=500),
):
    """Satellites above min_elev right now, highest first, with their RF layers attached.

    rf=only (default) restricts to satellites with an active SatNOGS transmitter or an FCC
    authorization, which is the question the endpoint exists to answer; rf=all screens every
    satellite with fresh elements, useful for orientation but silent on receivability.
    """
    rows, sats = _candidates(db, rf_only=(rf == "only"))
    if not rows:
        return {"observer": {"lat": lat, "lon": lon, "alt_m": alt_m}, "visible": [],
                "note": "no candidates with fresh elements", "attribution": SATNOGS_ATTRIBUTION}
    now = dt.datetime.now(dt.timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute,
                  now.second + now.microsecond / 1e6)
    err, r_teme, _ = sats.sgp4(np.array([jd]), np.array([fr]))
    r = r_teme[:, 0, :]
    ok = err[:, 0] == 0
    az, el, rng = _az_el_range(r, np.array([jd + fr]), lat, lon, alt_m)
    visible_idx = [i for i in np.argsort(-el) if ok[i] and el[i] >= min_elev][:limit]
    ids = [int(rows[i]["norad_id"]) for i in visible_idx]
    rf_map = _rf_payload(db, ids)
    visible = []
    for i in visible_idx:
        row = rows[i]
        n = int(row["norad_id"])
        visible.append(
            {
                "norad_id": n,
                "name": row["canonical_name"],
                "elevation_deg": round(float(el[i]), 1),
                "azimuth_deg": round(float(az[i]), 1),
                "range_km": round(float(rng[i]), 1),
                "element_epoch": row["epoch"].isoformat(),
                "satnogs": rf_map[n]["transmitters"],
                "fcc": rf_map[n]["fcc_authorizations"],
            }
        )
    return {
        "observer": {"lat": lat, "lon": lon, "alt_m": alt_m, "min_elev": min_elev},
        "computed_at": now.isoformat(),
        "candidates_screened": len(rows),
        "visible": visible,
        "attribution": SATNOGS_ATTRIBUTION,
    }


@router.get("/passes")
def passes(
    db=Depends(get_db),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    norad: int = Query(..., ge=1),
    alt_m: float = Query(0.0, ge=-500, le=9000),
    hours: int = Query(24, ge=1, le=72),
    min_elev: float = Query(10.0, ge=0, le=90),
):
    """Pass windows for one satellite: rise, peak, set, with azimuths for antenna pointing.

    Coarse 30-second scan over the window, which cannot miss a usable amateur pass (LEO passes
    above 10 degrees last minutes; a 15-second peak refinement is below element accuracy)."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT g.*, s.canonical_name FROM gp_elements g
            JOIN satellite s ON s.norad_id = g.norad_id
            WHERE g.norad_id = %(n)s AND g.epoch >= now() - %(age_days)s * interval '1 day'
            ORDER BY g.epoch DESC LIMIT 1
            """,
            {"n": norad, "age_days": MAX_ELEMENT_AGE_DAYS},
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no fresh elements for that norad id")
    sat = _build_satrec(row)
    now = dt.datetime.now(dt.timezone.utc)
    step_s = 30.0
    n_steps = int(hours * 3600 / step_s)
    jd0, fr0 = jday(now.year, now.month, now.day, now.hour, now.minute, float(now.second))
    fr = fr0 + np.arange(n_steps) * (step_s / 86400.0)
    jd = np.full(n_steps, jd0)
    err, r_teme, _ = SatrecArray([sat]).sgp4(jd, fr)
    az, el, rng = _az_el_range(r_teme[0], jd + fr, lat, lon, alt_m)
    above = el >= min_elev
    out = []
    i = 0
    while i < n_steps:
        if above[i] and err[0][i] == 0:
            j = i
            while j + 1 < n_steps and above[j + 1]:
                j += 1
            seg = slice(i, j + 1)
            peak = i + int(np.argmax(el[seg]))
            t0 = now + dt.timedelta(seconds=i * step_s)
            t1 = now + dt.timedelta(seconds=j * step_s)
            tp = now + dt.timedelta(seconds=peak * step_s)
            out.append(
                {
                    "rise": t0.isoformat(),
                    "rise_azimuth_deg": round(float(az[i]), 1),
                    "peak": tp.isoformat(),
                    "peak_elevation_deg": round(float(el[peak]), 1),
                    "peak_azimuth_deg": round(float(az[peak]), 1),
                    "set": t1.isoformat(),
                    "set_azimuth_deg": round(float(az[j]), 1),
                    "duration_s": int((j - i) * step_s),
                }
            )
            i = j + 1
        else:
            i += 1
    rf_map = _rf_payload(db, [norad])
    return {
        "norad_id": norad,
        "name": row["canonical_name"],
        "observer": {"lat": lat, "lon": lon, "alt_m": alt_m, "min_elev": min_elev},
        "window_hours": hours,
        "element_epoch": row["epoch"].isoformat(),
        "passes": out,
        "satnogs": rf_map[norad]["transmitters"],
        "fcc": rf_map[norad]["fcc_authorizations"],
        "attribution": SATNOGS_ATTRIBUTION,
    }
