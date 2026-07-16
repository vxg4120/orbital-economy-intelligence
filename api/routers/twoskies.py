"""GET /api/twoskies/* -- the Two Skies bridge: the satellite catalog meets the exoplanet catalog.

One view over two databases. The satellite identity graph (``oei``) knows where ~16k LEO objects
are right now; the exoplanet identity graph (``exo``) knows where the interesting targets are on the
sky. This module joins them: given an exoplanet host star's line of sight and a ground observatory,
which tracked satellites cross near it, and when.

The honest science (framed everywhere, never overclaimed):
  * TESS itself is largely immune. TESS observes from a high lunar-resonant orbit far above LEO;
    megaconstellations do not streak its frames. The real contamination target is GROUND-BASED
    follow-up -- the TFOP network and wide-field surveys that confirm TESS candidates from Earth.
  * Positions are ILLUSTRATIVE. The element sets are up to ~1 week old, so propagated positions
    carry along-track error of many km (arcminutes-to-degrees on the sky). This answers "roughly
    which objects sweep this patch of sky", not operational, collision-grade questions.
  * Frames are approximate: TEME is treated as inertial (no precession/nutation), no refraction.

Three endpoints:
  * ``/targets``               -- curated exoplanet host stars (famous + conflict-flagged) with coords.
  * ``/congestion-astronomy``  -- the megaconstellation-vs-astronomy panel (adapts the congestion query).
  * ``/passes``                -- THE bridge: SGP4-propagate the LEO catalog over a window and return
                                  satellites whose apparent (topocentric) position passes within
                                  ``sep_deg`` of the target line of sight while the target is up.

Compute is bounded: the LEO-payload catalog (~16k objects) is cached as pre-built SGP4 records and
propagated with a single vectorised ``SatrecArray`` call over a coarse time grid, so a request runs
in ~1-2s, not minutes. Timing is logged.
"""

import datetime as dt
import logging
import math
import threading
import time
from collections import Counter

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sgp4.api import WGS72, Satrec, SatrecArray

from api.deps import get_db, get_exo_db
from api.routers.congestion import _CONGESTION_SQL

router = APIRouter(prefix="/twoskies", tags=["twoskies"])
log = logging.getLogger("twoskies")

_TWO_PI = 2.0 * math.pi
# Julian date of 1949-12-31 00:00 UT -- SGP4's epoch origin (sgp4init `epoch` is days since then).
_JD_1949 = 2433281.5
# WGS84 ellipsoid (km).
_WGS84_A = 6378.137
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)

CAVEATS = [
    "TESS itself is largely immune: it observes from a high lunar-resonant orbit far above LEO, so "
    "megaconstellations do not streak its frames. The contamination risk is to GROUND-BASED "
    "follow-up -- the TFOP network and wide-field surveys that confirm TESS candidates from Earth.",
    "Positions are ILLUSTRATIVE, not operational. The satellite element sets are up to ~1 week old; "
    "propagated positions carry along-track error of many kilometres (arcminutes-to-degrees on the "
    "sky). Good for 'roughly which objects cross this patch of sky', not collision-grade avoidance.",
    "Only LEO payloads are propagated (the streak/interference source). Coordinate frames are "
    "approximate -- TEME treated as inertial, no precession/nutation/refraction correction.",
]

# ---------------------------------------------------------------------------------------------
# Observatory presets. Real ground-based follow-up / survey sites; a spread of latitudes so both
# hemispheres' targets are reachable. (name, lat_deg, lon_deg_east, elevation_km).
# ---------------------------------------------------------------------------------------------
SITES: dict[str, tuple[str, float, float, float]] = {
    "kitt_peak": ("Kitt Peak, Arizona (TFOP)", 31.9583, -111.5967, 2.096),
    "paranal": ("Cerro Paranal / VLT, Chile", -24.6275, -70.4044, 2.635),
    "mauna_kea": ("Maunakea, Hawaii", 19.8207, -155.4681, 4.205),
    "la_palma": ("Roque de los Muchachos, La Palma", 28.7606, -17.8814, 2.396),
    "siding_spring": ("Siding Spring, Australia (TFOP)", -31.2733, 149.0644, 1.165),
    "sutherland": ("SAAO Sutherland, South Africa", -32.3790, 20.8110, 1.798),
    "generic_north": ("Generic mid-northern site (35N)", 35.0, 0.0, 0.5),
}
DEFAULT_SITE = "kitt_peak"

# ---------------------------------------------------------------------------------------------
# Curated exoplanet targets. Famous host stars (nearby M-dwarf / benchmark systems) plus the
# conflict-flagged ones surfaced from `exo`. Names are matched verbatim against star.canonical_name;
# any not present in the DB are silently skipped, so the list can be generous.
# ---------------------------------------------------------------------------------------------
FAMOUS_HOSTS = [
    "TRAPPIST-1", "TOI-700", "TOI-270", "LHS 475", "LTT 1445 A", "GJ 1132", "GJ 1214", "GJ 357",
    "GJ 436", "GJ 486", "GJ 667 C", "GJ 9827", "GJ 3470", "GJ 3929", "GJ 367", "GJ 806", "GJ 1252",
    "K2-18", "K2-141", "55 Cnc", "HD 209458", "HD 189733", "WASP-12", "WASP-121", "WASP-39",
    "WASP-127", "L 98-59", "TOI-561", "TOI-1338", "TOI-849", "HAT-P-11", "HD 3167", "K2-138",
    "AU Mic", "LP 890-9",
]

# ---------------------------------------------------------------------------------------------
# Exoplanet-target queries (read-only, `exo`). Only the identity tables star + candidate are read;
# a host's conflict flag is the presence of an AMBIGUOUS-disposition candidate -- the resolved
# stand-in for "the source catalogs disagree about whether this is a real planet" (a nod to the
# ExoDossier conflict layer). Aggregation collapses each host to one sky point.
# ---------------------------------------------------------------------------------------------
_TARGETS_SQL = """
SELECT
    s.star_id,
    s.canonical_name                                  AS host,
    s.tic_id,
    s.ra_deg::float8                                  AS ra_deg,
    s.dec_deg::float8                                 AS dec_deg,
    count(*)                                          AS n_candidates,
    array_agg(c.canonical_name ORDER BY c.canonical_name)  AS candidate_names,
    array_agg(c.disposition   ORDER BY c.canonical_name)   AS dispositions
FROM star s
JOIN candidate c ON c.star_id = s.star_id
WHERE s.ra_deg IS NOT NULL AND s.dec_deg IS NOT NULL
  AND ({where})
GROUP BY s.star_id, s.canonical_name, s.tic_id, s.ra_deg, s.dec_deg
"""

# Representative disposition per host: best-known planet wins the label; AMBIGUOUS marks a conflict.
_DISP_RANK = {
    "CONFIRMED": 0, "KNOWN_PLANET": 1, "CANDIDATE": 2, "AMBIGUOUS": 3, "FALSE_POSITIVE": 4,
}


def _reduce_target(row: dict, category: str) -> dict:
    names = row["candidate_names"]
    disps = [d or "UNKNOWN" for d in row["dispositions"]]
    has_conflict = any(d == "AMBIGUOUS" for d in disps)
    # pick the representative (planet name, disposition) by best rank, ties by name order.
    best_i = min(range(len(names)), key=lambda i: (_DISP_RANK.get(disps[i], 9), names[i]))
    return {
        "candidate": names[best_i],
        "host": row["host"],
        "tic_id": row["tic_id"],
        "ra_deg": round(row["ra_deg"], 6),
        "dec_deg": round(row["dec_deg"], 6),
        "disposition": disps[best_i],
        "has_conflict": has_conflict,
        "n_candidates": row["n_candidates"],
        "category": category,
    }


@router.get("/targets")
def targets(db=Depends(get_exo_db), conflict_limit: int = Query(24, ge=0, le=200)):
    """Curated exoplanet host stars with sky coordinates for the line-of-sight forecaster.

    Returns the famous benchmark systems plus a slice of conflict-flagged hosts (a candidate with an
    AMBIGUOUS/disputed disposition). One row per host (a single point on the sky); ``has_conflict``
    lets the UI flag the ones where the source catalogs disagree.
    """
    with db.cursor() as cur:
        cur.execute(
            _TARGETS_SQL.format(where="s.canonical_name = ANY(%(names)s)"),
            {"names": FAMOUS_HOSTS},
        )
        famous_rows = cur.fetchall()

        cur.execute(
            _TARGETS_SQL.format(
                where="s.star_id IN (SELECT star_id FROM candidate WHERE disposition = 'AMBIGUOUS') "
                "AND s.canonical_name <> ALL(%(names)s)"
            ),
            {"names": FAMOUS_HOSTS},
        )
        conflict_rows = cur.fetchall()

    famous = sorted(
        (_reduce_target(r, "famous") for r in famous_rows), key=lambda t: t["host"]
    )
    conflict = sorted(
        (_reduce_target(r, "conflict") for r in conflict_rows), key=lambda t: t["host"]
    )[:conflict_limit]

    targets = famous + conflict
    return {
        "targets": targets,
        "n_famous": len(famous),
        "n_conflict": len(conflict),
        "note": "has_conflict = the host has a candidate with a disputed/AMBIGUOUS disposition "
        "(source catalogs disagree). Coordinates are ICRS from the exo star table.",
    }


# ---------------------------------------------------------------------------------------------
# Congestion-vs-astronomy panel. Reuses the LEO alt x inclination density bins (the heatmap) and
# adds the catalog-scale headline numbers + the operator concentration that drives the interference
# story. All read-only aggregates over `oei`.
# ---------------------------------------------------------------------------------------------
_ASTRO_SUMMARY_SQL = """
SELECT
    (SELECT count(*)                 FROM satellite) AS catalog_objects,
    (SELECT count(DISTINCT norad_id) FROM gp_elements) AS tracked_with_elements,
    (SELECT count(*) FROM satellite
        WHERE object_type = 'PAYLOAD' AND launch_date > current_date - 365) AS payloads_launched_1y,
    (SELECT count(*) FROM satellite
        WHERE object_type = 'PAYLOAD' AND launch_date > current_date - 30)  AS payloads_launched_30d
"""

_TOP_OPERATORS_SQL = """
SELECT o.canonical_name AS operator, count(*) AS payloads
FROM satellite s
JOIN satellite_operator so ON so.satellite_id = s.satellite_id
    AND so.role = 'owner' AND so.valid_to IS NULL
JOIN operator o ON o.operator_id = so.operator_id
WHERE s.object_type = 'PAYLOAD'
GROUP BY o.canonical_name
ORDER BY count(*) DESC, o.canonical_name
LIMIT 8
"""


@router.get("/congestion-astronomy")
def congestion_astronomy(db=Depends(get_db)):
    """The 'the sky is getting crowded' panel: catalog-scale numbers + LEO shell density + operators.

    Frames the megaconstellation-vs-astronomy interference story. The altitude x inclination bins are
    the same density proxy the Overview congestion heatmap uses; shells roll them up into ~200 km
    bands. This is catalog density, not conjunction data.
    """
    with db.cursor() as cur:
        cur.execute(_CONGESTION_SQL)
        bins = cur.fetchall()
        cur.execute(_ASTRO_SUMMARY_SQL)
        summary = cur.fetchone()
        cur.execute(_TOP_OPERATORS_SQL)
        top_operators = cur.fetchall()

    # Roll the 50 km bins into 200 km shells (Python -- no extra scan). Also the LEO total + peak.
    leo_objects = 0
    peak = {"alt_bin_km": None, "inc_bin_deg": None, "object_count": 0}
    shells: dict[int, int] = {}
    for b in bins:
        leo_objects += b["object_count"]
        if b["object_count"] > peak["object_count"]:
            peak = dict(b)
        band = (b["alt_bin_km"] // 200) * 200
        shells[band] = shells.get(band, 0) + b["object_count"]

    shell_rows = [
        {"alt_lo_km": lo, "alt_hi_km": lo + 200, "objects": n}
        for lo, n in sorted(shells.items())
    ]

    return {
        "catalog_objects": summary["catalog_objects"],
        "tracked_with_elements": summary["tracked_with_elements"],
        "leo_objects": leo_objects,
        "payloads_launched_1y": summary["payloads_launched_1y"],
        "payloads_launched_30d": summary["payloads_launched_30d"],
        "top_operators": top_operators,
        "shells": shell_rows,
        "peak_bin": peak,
        "bins": bins,
        "caveats": CAVEATS,
        "note": "IAU Centre for the Protection of the Dark and Quiet Sky from Satellite "
        "Constellation Interference (IAU CPS) coordinates the community response to this issue.",
    }


# ---------------------------------------------------------------------------------------------
# The bridge: /passes. SGP4 over the LEO catalog, topocentric separation from the target sightline.
# ---------------------------------------------------------------------------------------------
# Latest element set per NORAD for LEO payloads, joined to identity for labels. LATERAL index-seeks
# per NORAD (gp_elements PK (norad_id, epoch, source)) -- bounded, ~0.7s -- never a catalog-wide
# DISTINCT ON scan. perigee_km/apogee_km are STORED generated columns.
_FETCH_LEO_SQL = """
WITH le AS (
    SELECT s.satellite_id, s.norad_id, s.canonical_name, g.epoch,
           g.mean_motion, g.eccentricity, g.inclination, g.ra_of_asc_node,
           g.arg_of_pericenter, g.mean_anomaly, g.bstar, g.perigee_km, g.apogee_km
    FROM satellite s
    JOIN LATERAL (
        SELECT epoch, mean_motion, eccentricity, inclination, ra_of_asc_node,
               arg_of_pericenter, mean_anomaly, bstar, perigee_km, apogee_km
        FROM gp_elements g
        WHERE g.norad_id = s.norad_id
        ORDER BY epoch DESC
        LIMIT 1
    ) g ON TRUE
    WHERE s.norad_id IS NOT NULL AND s.object_type = 'PAYLOAD'
      AND (g.perigee_km + g.apogee_km) / 2.0 < %(maxalt)s
)
SELECT * FROM le
"""

# Cached pre-built SGP4 catalog. Element sets refresh at most every ~2h upstream, so a 30-min TTL is
# ample; rebuilding costs the ~0.7s fetch + a fast satrec build. Guarded for the threaded sync pool.
_CACHE: dict = {"built_at": 0.0, "arr": None, "meta": None, "norads": None}
_CACHE_TTL_S = 1800.0
_CACHE_LOCK = threading.Lock()


def _to_jd(t: dt.datetime) -> float:
    """UTC datetime -> Julian date."""
    t = t.astimezone(dt.timezone.utc)
    frac = (t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6) / 86400.0
    return t.toordinal() + 1721424.5 + frac


def _gmst_rad(jd_ut1: float) -> float:
    """Greenwich Mean Sidereal Time (radians) for the TEME->ECEF rotation. UT1 ~ UTC (sub-second)."""
    d = jd_ut1 - 2451545.0
    tc = d / 36525.0
    deg = 280.46061837 + 360.98564736629 * d + 0.000387933 * tc * tc - (tc * tc * tc) / 38710000.0
    return math.radians(deg % 360.0)


def _observer_ecef(lat_deg: float, lon_deg: float, elev_km: float) -> tuple[np.ndarray, np.ndarray]:
    """Observer ECEF position (km) and geodetic 'up' unit vector for a WGS84 site."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    n = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * math.sin(lat) ** 2)
    cl, sl = math.cos(lat), math.sin(lat)
    co, so = math.cos(lon), math.sin(lon)
    pos = np.array([(n + elev_km) * cl * co, (n + elev_km) * cl * so, (n * (1 - _WGS84_E2) + elev_km) * sl])
    up = np.array([cl * co, cl * so, sl])
    return pos, up


def _build_satrecs(rows: list[dict]) -> tuple[SatrecArray, list[dict], np.ndarray]:
    """Build SGP4 records + parallel metadata from OMM mean elements. Bad rows are skipped."""
    satrecs: list[Satrec] = []
    meta: list[dict] = []
    for r in rows:
        try:
            epoch_days = _to_jd(r["epoch"]) - _JD_1949
            s = Satrec()
            s.sgp4init(
                WGS72, "i", int(r["norad_id"]), epoch_days,
                float(r["bstar"] or 0.0), 0.0, 0.0,
                float(r["eccentricity"]),
                math.radians(float(r["arg_of_pericenter"])),
                math.radians(float(r["inclination"])),
                math.radians(float(r["mean_anomaly"])),
                float(r["mean_motion"]) * _TWO_PI / 1440.0,
                math.radians(float(r["ra_of_asc_node"])),
            )
        except (ValueError, TypeError):
            continue
        satrecs.append(s)
        meta.append({
            "norad": int(r["norad_id"]),
            "name": r["canonical_name"],
            "satellite_id": r["satellite_id"],
            "alt_km": round((float(r["perigee_km"]) + float(r["apogee_km"])) / 2.0, 1),
        })
    norads = np.array([m["norad"] for m in meta])
    return SatrecArray(satrecs), meta, norads


def _get_catalog(db) -> tuple[SatrecArray, list[dict], np.ndarray, bool]:
    now = time.time()
    with _CACHE_LOCK:
        if _CACHE["arr"] is not None and now - _CACHE["built_at"] < _CACHE_TTL_S:
            return _CACHE["arr"], _CACHE["meta"], _CACHE["norads"], False
    t0 = time.time()
    with db.cursor() as cur:
        cur.execute(_FETCH_LEO_SQL, {"maxalt": 2000})
        rows = cur.fetchall()
    arr, meta, norads = _build_satrecs(rows)
    log.info("twoskies: built %d satrecs from %d rows in %.2fs", len(meta), len(rows), time.time() - t0)
    with _CACHE_LOCK:
        _CACHE.update(built_at=time.time(), arr=arr, meta=meta, norads=norads)
    return arr, meta, norads, True


def _parse_datetime(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


@router.get("/passes")
def passes(
    db=Depends(get_db),
    ra: float = Query(..., ge=0.0, le=360.0, description="Target right ascension, degrees (ICRS)"),
    dec: float = Query(..., ge=-90.0, le=90.0, description="Target declination, degrees (ICRS)"),
    datetime: str | None = Query(None, description="Window start, UTC ISO-8601. Default: now."),
    window_min: int = Query(60, ge=5, le=180, description="Window length, minutes."),
    sep_deg: float = Query(5.0, ge=0.1, le=30.0, description="Angular-separation threshold, degrees."),
    site: str = Query(DEFAULT_SITE, description=f"Observatory preset. One of: {', '.join(SITES)}."),
    lat: float | None = Query(None, ge=-90.0, le=90.0, description="Override site latitude, deg."),
    lon: float | None = Query(None, ge=-180.0, le=180.0, description="Override site longitude, deg E."),
    elev_km: float | None = Query(None, ge=-0.5, le=6.0, description="Override site elevation, km."),
    step_sec: int = Query(30, ge=15, le=120, description="Time step, seconds."),
    min_alt_deg: float = Query(20.0, ge=0.0, le=80.0, description="Only count while the target is "
                              "above this altitude (an observability gate)."),
    max_results: int = Query(300, ge=1, le=2000, description="Cap the returned pass list."),
):
    """Satellites whose apparent position passes within ``sep_deg`` of the target line of sight.

    SGP4-propagates the cached LEO-payload catalog over ``window_min`` at ``step_sec`` from the given
    UTC time, converts each object to the observatory's topocentric frame, and reports every object
    whose closest approach to the target sightline falls under the threshold *while the target is
    above ``min_alt_deg``* (you can only observe -- and be contaminated -- when the target is up).
    One row per satellite at its closest approach. ILLUSTRATIVE, not operational (see caveats).
    """
    if lat is not None and lon is not None:
        site_name = "Custom site"
        s_lat, s_lon, s_elev = lat, lon, (elev_km if elev_km is not None else 0.5)
    else:
        if site not in SITES:
            raise HTTPException(status_code=422, detail=f"unknown site {site!r}; presets: {', '.join(SITES)}")
        site_name, s_lat, s_lon, s_elev = SITES[site]
        if elev_km is not None:
            s_elev = elev_km

    start = _parse_datetime(datetime)
    steps = int(round(window_min * 60 / step_sec)) + 1
    t_wall = time.time()

    arr, meta, norads, rebuilt = _get_catalog(db)
    if not meta:
        raise HTTPException(status_code=503, detail="no LEO element sets available to propagate")

    obs_ecef, up = _observer_ecef(s_lat, s_lon, s_elev)

    # --- time grid (Julian date, split day + fraction for SGP4 precision) ---
    jd = np.empty(steps)
    fr = np.empty(steps)
    for i in range(steps):
        j = _to_jd(start + dt.timedelta(seconds=step_sec * i))
        jd[i] = math.floor(j - 0.5) + 0.5
        fr[i] = j - jd[i]

    t_prop = time.time()
    err, rpos, _ = arr.sgp4(jd, fr)  # rpos: (nsat, nt, 3) TEME km; err: (nsat, nt) codes
    prop_ms = (time.time() - t_prop) * 1000.0

    # --- TEME -> ECEF (rotate about z by GMST), then topocentric geometry ---
    gmst = np.array([_gmst_rad(jd[i] + fr[i]) for i in range(steps)])
    cg, sg = np.cos(gmst), np.sin(gmst)
    x, y, z = rpos[:, :, 0], rpos[:, :, 1], rpos[:, :, 2]
    xe = cg * x + sg * y
    ye = -sg * x + cg * y
    ze = z

    tx = xe - obs_ecef[0]
    ty = ye - obs_ecef[1]
    tz = ze - obs_ecef[2]
    rng = np.sqrt(tx * tx + ty * ty + tz * tz)
    ux, uy, uz = tx / rng, ty / rng, tz / rng
    sat_alt = np.arcsin(np.clip(ux * up[0] + uy * up[1] + uz * up[2], -1.0, 1.0))  # (nsat, nt)

    # Target sightline: ICRS unit vector, rotated into ECEF at each step (same GMST rotation).
    ra_r, dec_r = math.radians(ra), math.radians(dec)
    ti = np.array([math.cos(dec_r) * math.cos(ra_r), math.cos(dec_r) * math.sin(ra_r), math.sin(dec_r)])
    gx = cg * ti[0] + sg * ti[1]
    gy = -sg * ti[0] + cg * ti[1]
    gz = np.full(steps, ti[2])
    tgt_alt = np.degrees(np.arcsin(np.clip(gx * up[0] + gy * up[1] + gz * up[2], -1.0, 1.0)))  # (nt,)

    dot = np.clip(ux * gx[None, :] + uy * gy[None, :] + uz * gz[None, :], -1.0, 1.0)
    sep = np.degrees(np.arccos(dot))  # (nsat, nt)

    # Only count steps where the TARGET is observable and the satellite is above the horizon.
    target_up = tgt_alt >= min_alt_deg  # (nt,)
    qualifying = target_up[None, :] & (sat_alt > 0.0) & (err == 0)
    sep_masked = np.where(qualifying, sep, np.inf)
    closest = sep_masked.min(axis=1)  # (nsat,)

    target_max_alt = float(tgt_alt.max())
    target_visible = bool(target_up.any())

    hit_idx = np.nonzero(closest < sep_deg)[0]
    n_found = int(hit_idx.size)

    # Operator labels only for the hits (a small, targeted lookup -- keeps the catalog fetch lean).
    op_by_id: dict[int, str] = {}
    if n_found:
        sat_ids = [meta[i]["satellite_id"] for i in hit_idx]
        with db.cursor() as cur:
            cur.execute(
                "SELECT so.satellite_id, o.canonical_name AS operator "
                "FROM satellite_operator so JOIN operator o ON o.operator_id = so.operator_id "
                "WHERE so.role = 'owner' AND so.valid_to IS NULL AND so.satellite_id = ANY(%s)",
                (sat_ids,),
            )
            op_by_id = {r["satellite_id"]: r["operator"] for r in cur.fetchall()}

    passes_out: list[dict] = []
    tally: Counter[str] = Counter()
    for i in hit_idx:
        j = int(np.argmin(sep_masked[i]))
        m = meta[i]
        operator = op_by_id.get(m["satellite_id"])
        tally[operator or "Unattributed"] += 1
        passes_out.append({
            "norad": m["norad"],
            "name": m["name"],
            "operator": operator,
            "alt_km": m["alt_km"],
            "closest_sep_deg": round(float(closest[i]), 3),
            "alt_deg": round(float(math.degrees(sat_alt[i, j])), 2),
            "time_utc": (start + dt.timedelta(seconds=step_sec * j)).isoformat().replace("+00:00", "Z"),
        })

    passes_out.sort(key=lambda p: p["closest_sep_deg"])
    elapsed_ms = round((time.time() - t_wall) * 1000.0, 1)
    log.info(
        "twoskies /passes ra=%.3f dec=%.3f site=%s window=%dmin step=%ds sats=%d found=%d "
        "prop=%.0fms total=%.0fms rebuilt=%s",
        ra, dec, site, window_min, step_sec, len(meta), n_found, prop_ms, elapsed_ms, rebuilt,
    )

    operator_tally = [
        {"operator": op, "count": n} for op, n in tally.most_common()
    ]

    return {
        "target": {"ra_deg": ra, "dec_deg": dec},
        "site": {"key": None if (lat is not None and lon is not None) else site,
                 "name": site_name, "lat_deg": s_lat, "lon_deg": s_lon, "elev_km": s_elev},
        "window": {
            "start_utc": start.isoformat().replace("+00:00", "Z"),
            "end_utc": (start + dt.timedelta(minutes=window_min)).isoformat().replace("+00:00", "Z"),
            "window_min": window_min,
            "step_sec": step_sec,
            "steps": steps,
        },
        "sep_deg": sep_deg,
        "min_alt_deg": min_alt_deg,
        "target_visible": target_visible,
        "target_max_alt_deg": round(target_max_alt, 1),
        "n_considered": len(meta),
        "n_found": n_found,
        "truncated": n_found > max_results,
        "passes": passes_out[:max_results],
        "operator_tally": operator_tally,
        "elapsed_ms": elapsed_ms,
        "caveats": CAVEATS,
    }
