"""Entity resolution: deterministic (NORAD, COSPAR) then probabilistic (name/launch/regime).

Reads the latest OK snapshot per source from the raw landing tables, creates/updates
``satellite`` rows, and writes the ``satellite_identifier`` crosswalk via identity.merge.link
(so every link is audited in merge_log). Borderline probabilistic matches are parked in a
human-review CSV instead of being linked. No commit here — the caller owns the transaction.
"""

from __future__ import annotations

import csv
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from identity import merge
from identity.normalize import norm_cospar, norm_name, orbital_regime, parse_date_loose

_CONFIG_DEFAULT = Path(__file__).with_name("match_config.yml")
_REVIEW_DEFAULT = Path("data/review/match_review.csv")

_REVIEW_HEADER = [
    "probe_source",
    "probe_key",
    "probe_name",
    "candidate_satellite_id",
    "candidate_name",
    "score",
    "name_sim",
    "launch_days",
    "regime_probe",
    "regime_candidate",
]

# Loose country canonicalization so "USA"/"US"/"United States" don't read as a mismatch.
_COUNTRY = {
    "US": "US", "USA": "US", "UNITED STATES": "US",
    "UK": "UK", "GBR": "UK", "UNITED KINGDOM": "UK",
    "PRC": "CN", "CN": "CN", "CHINA": "CN",
    "CIS": "RU", "RU": "RU", "RUS": "RU", "RUSSIA": "RU", "USSR": "RU",
    "FR": "FR", "FRA": "FR", "FRANCE": "FR",
    "JPN": "JP", "JP": "JP", "JAPAN": "JP",
    "CA": "CA", "CAN": "CA", "CANADA": "CA",
    "IND": "IN", "IN": "IN", "INDIA": "IN",
    "LUXE": "LU", "LUXEMBOURG": "LU",
    "FIN": "FI", "FINLAND": "FI",
}


def _country_code(s: str | None) -> str | None:
    if not s:
        return None
    return _COUNTRY.get(str(s).strip().upper())


def _latest_run(conn, table: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT max(r.ingest_run_id) FROM {table} r "
            "JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'"
        )
        return cur.fetchone()[0]


def _upsert_norad(cur, norad, cospar, name, obj_type, launch) -> int:
    cur.execute(
        """
        INSERT INTO satellite (norad_id, cospar_id, canonical_name, object_type, launch_date)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (norad_id) DO UPDATE SET updated_at = now()
        RETURNING satellite_id
        """,
        (norad, cospar, name or str(norad), obj_type or "UNKNOWN", launch),
    )
    return cur.fetchone()[0]


def _create_satellite(cur, cospar, name, obj_type, launch) -> int:
    cur.execute(
        """
        INSERT INTO satellite (norad_id, cospar_id, canonical_name, object_type, launch_date)
        VALUES (NULL, %s, %s, %s, %s)
        RETURNING satellite_id
        """,
        (cospar, name or cospar or "UNKNOWN", obj_type or "UNKNOWN", launch),
    )
    return cur.fetchone()[0]


def _find_by_cospar(cur, cospar: str) -> int | None:
    cur.execute(
        "SELECT satellite_id FROM satellite_identifier "
        "WHERE id_type = 'cospar' AND id_value = %s LIMIT 1",
        (cospar,),
    )
    row = cur.fetchone()
    return row[0] if row else None


# --- deterministic passes -----------------------------------------------------


def deterministic(conn) -> None:
    """NORAD-exact then COSPAR-exact linking across the latest snapshot of each source."""
    _deterministic_satcat(conn)
    _deterministic_gcat_norad(conn)
    _deterministic_ucs_norad(conn)
    _cospar_pass(conn)


def _deterministic_satcat(conn) -> None:
    run = _latest_run(conn, "raw_satcat")
    if run is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT norad_cat_id, object_id, object_name, object_type, launch_date, "
            "apogee, perigee FROM raw_satcat WHERE ingest_run_id = %s",
            (run,),
        )
        rows = cur.fetchall()
    for norad, object_id, name, obj_type, launch, _apogee, _perigee in rows:
        cospar, _ = norm_cospar(object_id)
        with conn.cursor() as cur:
            sat_id = _upsert_norad(cur, norad, cospar, name, obj_type, launch)
        merge.link(conn, sat_id, {"id_type": "norad", "id_value": str(norad),
                                  "source": "satcat"}, "norad_exact", 1.000)
        if cospar:
            merge.link(conn, sat_id, {"id_type": "cospar", "id_value": cospar,
                                      "source": "satcat"}, "norad_exact", 1.000)
        if name:
            merge.link(conn, sat_id, {"id_type": "name_satcat", "id_value": name,
                                      "source": "satcat"}, "norad_exact", 1.000)


def _deterministic_gcat_norad(conn) -> None:
    run = _latest_run(conn, "raw_gcat_satcat")
    if run is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT jcat, norad_id, piece, object_type, name, pl_name, launch_date "
            "FROM raw_gcat_satcat WHERE ingest_run_id = %s AND norad_id IS NOT NULL",
            (run,),
        )
        rows = cur.fetchall()
    for jcat, norad, piece, obj_type, name, pl_name, launch in rows:
        cospar, _ = norm_cospar(piece)
        with conn.cursor() as cur:
            sat_id = _upsert_norad(cur, norad, cospar, pl_name or name,
                                   obj_type, parse_date_loose(launch))
        merge.link(conn, sat_id, {"id_type": "gcat_id", "id_value": jcat,
                                  "source": "gcat"}, "norad_exact", 1.000)
        gname = pl_name or name
        if gname:
            merge.link(conn, sat_id, {"id_type": "name_gcat", "id_value": gname,
                                      "source": "gcat"}, "norad_exact", 1.000)
        if cospar:
            merge.link(conn, sat_id, {"id_type": "cospar", "id_value": cospar,
                                      "source": "gcat"}, "norad_exact", 1.000)


def _deterministic_ucs_norad(conn) -> None:
    run = _latest_run(conn, "raw_ucs")
    if run is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT row_key, norad_id, cospar_id, name, launch_date "
            "FROM raw_ucs WHERE ingest_run_id = %s AND norad_id IS NOT NULL",
            (run,),
        )
        rows = cur.fetchall()
    for row_key, norad, cospar_id, name, launch in rows:
        cospar, _ = norm_cospar(cospar_id)
        with conn.cursor() as cur:
            sat_id = _upsert_norad(cur, norad, cospar, name, None, parse_date_loose(launch))
        merge.link(conn, sat_id, {"id_type": "ucs_row", "id_value": row_key,
                                  "source": "ucs"}, "norad_exact", 1.000)
        if cospar:
            merge.link(conn, sat_id, {"id_type": "cospar", "id_value": cospar,
                                      "source": "ucs"}, "norad_exact", 1.000)


def _cospar_pass(conn) -> None:
    """Link NORAD-less rows whose normalized COSPAR is standard and already known/creatable."""
    grun = _latest_run(conn, "raw_gcat_satcat")
    if grun is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT jcat, piece, object_type, name, pl_name, launch_date "
                "FROM raw_gcat_satcat WHERE ingest_run_id = %s AND norad_id IS NULL",
                (grun,),
            )
            rows = cur.fetchall()
        for jcat, piece, obj_type, name, pl_name, launch in rows:
            cospar, standard = norm_cospar(piece)
            if not (cospar and standard):
                continue
            with conn.cursor() as cur:
                sat_id = _find_by_cospar(cur, cospar)
                if sat_id is None:
                    sat_id = _create_satellite(cur, cospar, pl_name or name,
                                               obj_type, parse_date_loose(launch))
            merge.link(conn, sat_id, {"id_type": "cospar", "id_value": cospar,
                                      "source": "gcat"}, "cospar_exact", 1.000)
            merge.link(conn, sat_id, {"id_type": "gcat_id", "id_value": jcat,
                                      "source": "gcat"}, "cospar_exact", 1.000)

    urun = _latest_run(conn, "raw_ucs")
    if urun is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT row_key, cospar_id, name, launch_date FROM raw_ucs "
                "WHERE ingest_run_id = %s AND norad_id IS NULL AND cospar_id IS NOT NULL",
                (urun,),
            )
            rows = cur.fetchall()
        for row_key, cospar_id, name, launch in rows:
            cospar, standard = norm_cospar(cospar_id)
            if not (cospar and standard):
                continue
            with conn.cursor() as cur:
                sat_id = _find_by_cospar(cur, cospar)
                if sat_id is None:
                    sat_id = _create_satellite(cur, cospar, name, None,
                                               parse_date_loose(launch))
            merge.link(conn, sat_id, {"id_type": "cospar", "id_value": cospar,
                                      "source": "ucs"}, "cospar_exact", 1.000)
            merge.link(conn, sat_id, {"id_type": "ucs_row", "id_value": row_key,
                                      "source": "ucs"}, "cospar_exact", 1.000)


# --- probabilistic pass -------------------------------------------------------


def _candidate_profiles(conn) -> list[dict]:
    """Profiles for probabilistic candidates, drawn from the SATCAT snapshot (has altitudes)."""
    run = _latest_run(conn, "raw_satcat")
    if run is None:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT si.satellite_id, r.object_name, r.launch_date, r.perigee, r.apogee, r.owner
            FROM raw_satcat r
            JOIN satellite_identifier si
              ON si.id_type = 'norad' AND si.source = 'satcat'
             AND si.id_value = r.norad_cat_id::text
            WHERE r.ingest_run_id = %s
            """,
            (run,),
        )
        rows = cur.fetchall()
    profiles = []
    for sat_id, name, launch, perigee, apogee, owner in rows:
        profiles.append(
            {
                "satellite_id": sat_id,
                "name": name,
                "norm": norm_name(name),
                "launch": parse_date_loose(launch),
                "regime": orbital_regime(perigee, apogee),
                "country": _country_code(owner),
            }
        )
    return profiles


def _probes(conn) -> list[dict]:
    """NORAD-less, COSPAR-less rows from GCAT and UCS that need fuzzy resolution."""
    probes: list[dict] = []
    grun = _latest_run(conn, "raw_gcat_satcat")
    if grun is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT jcat, piece, name, pl_name, launch_date, perigee_km, apogee_km, state "
                "FROM raw_gcat_satcat WHERE ingest_run_id = %s AND norad_id IS NULL",
                (grun,),
            )
            for jcat, piece, name, pl_name, launch, perigee, apogee, state in cur.fetchall():
                cospar, standard = norm_cospar(piece)
                if cospar and standard:
                    continue  # handled by the COSPAR pass
                nm = pl_name or name
                probes.append(
                    {
                        "source": "gcat",
                        "id_type": "gcat_id",
                        "id_value": jcat,
                        "name": nm,
                        "norm": norm_name(nm),
                        "launch": parse_date_loose(launch),
                        "regime": orbital_regime(perigee, apogee),
                        "country": _country_code(state),
                    }
                )
    urun = _latest_run(conn, "raw_ucs")
    if urun is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT row_key, name, launch_date, country_operator FROM raw_ucs "
                "WHERE ingest_run_id = %s AND norad_id IS NULL AND cospar_id IS NULL",
                (urun,),
            )
            for row_key, name, launch, country in cur.fetchall():
                probes.append(
                    {
                        "source": "ucs",
                        "id_type": "ucs_row",
                        "id_value": row_key,
                        "name": name,
                        "norm": norm_name(name),
                        "launch": parse_date_loose(launch),
                        "regime": "UNKNOWN",
                        "country": _country_code(country),
                    }
                )
    return probes


def _score(probe: dict, cand: dict, weights: dict, window_days: int):
    """Return (score, name_sim, launch_days) or None if a hard consistency gate rejects."""
    # Regime gate: both known and different -> impossible match (GEO comsat vs LEO cubesat).
    if probe["regime"] != "UNKNOWN" and cand["regime"] != "UNKNOWN":
        if probe["regime"] != cand["regime"]:
            return None
    # Country gate: both confidently known and different -> reject.
    if probe["country"] and cand["country"] and probe["country"] != cand["country"]:
        return None

    name_sim = SequenceMatcher(None, probe["norm"], cand["norm"]).ratio()

    parts: list[tuple[float, float]] = [(weights["name"], name_sim)]  # (weight, signal)

    launch_days = None
    if probe["launch"] and cand["launch"]:
        launch_days = abs((probe["launch"] - cand["launch"]).days)
        if launch_days > window_days:
            return None  # launch-window gate
        parts.append((weights["launch"], 1.0 - launch_days / window_days))

    if probe["regime"] != "UNKNOWN" and cand["regime"] != "UNKNOWN":
        parts.append((weights["regime"], 1.0 if probe["regime"] == cand["regime"] else 0.0))

    total_w = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / total_w  # redistribute over available signals
    return (score, name_sim, launch_days)


def probabilistic(conn, config: dict, review_csv: Path) -> dict:
    """Fuzzy-resolve leftover probes; auto-link, park for review, or leave unmatched."""
    weights = config["weights"]
    auto = config["thresholds"]["auto_link"]
    low = config["thresholds"]["review_low"]
    window = config["launch_window_days"]

    profiles = _candidate_profiles(conn)
    stats = {"auto_links": 0, "review_rows": 0, "unmatched": 0}
    review_rows: list[list] = []

    for probe in _probes(conn):
        best = None
        for cand in profiles:
            scored = _score(probe, cand, weights, window)
            if scored is None:
                continue
            if best is None or scored[0] > best[0]:
                best = (scored[0], scored[1], scored[2], cand)
        if best is None:
            stats["unmatched"] += 1
            continue
        score, name_sim, launch_days, cand = best
        if score >= auto:
            merge.link(
                conn,
                cand["satellite_id"],
                {"id_type": probe["id_type"], "id_value": probe["id_value"],
                 "source": probe["source"], "confidence": round(score, 2)},
                "name_fuzzy>=0.92",
                round(score, 3),
                details={"probe": probe["name"], "candidate": cand["name"],
                         "name_sim": round(name_sim, 3)},
            )
            stats["auto_links"] += 1
        elif score >= low:
            review_rows.append(
                [probe["source"], probe["id_value"], probe["name"], cand["satellite_id"],
                 cand["name"], round(score, 3), round(name_sim, 3),
                 "" if launch_days is None else launch_days, probe["regime"], cand["regime"]]
            )
            stats["review_rows"] += 1
        else:
            stats["unmatched"] += 1

    if review_rows:
        _append_review(review_csv, review_rows)
    return stats


def _append_review(path: Path, rows: list[list]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(_REVIEW_HEADER)
        writer.writerows(rows)


def load_config(config_path=None) -> dict:
    with open(config_path or _CONFIG_DEFAULT) as fh:
        return yaml.safe_load(fh)


def run_matchers(conn, config_path=None, review_csv=None) -> dict:
    """Full matcher: deterministic passes then the probabilistic pass. Returns prob stats."""
    config = load_config(config_path)
    deterministic(conn)
    return probabilistic(conn, config, Path(review_csv or _REVIEW_DEFAULT))
