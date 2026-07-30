"""RF authorization build: resolve FCC grants onto canonical satellites.

Rebuilds satellite_fcc_authorization from the latest OK raw_fcc_ssal snapshot in two tiers,
each recording its match method per row (docs/design/0003-rf-authorization-layer.md):

  tier 'constellation': curated blanket-license mappings from identity/fcc_constellations.yml.
      One NGSO grant covers every satellite in the system, so the join is a name-prefix rule
      that a human wrote down and tests pin.
  tier 'gso_name': an SSAL satellite name whose normalized form matches exactly one canonical
      satellite, and that satellite matches no other SSAL name (unique both directions).
      Ambiguity means no row: absence over guesses, same as the rest of the identity layer.

Grants the tiers cannot place simply do not appear, and the run summary prints how many, so
coverage is a measured number rather than an implication. SatNOGS transmitters need no build
step at all: satellite.norad_id joins raw_satnogs_transmitters directly.

Safe to re-run at any time (full DELETE + rebuild, same convention as build_bus).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from common.db import get_conn  # noqa: E402
from identity.normalize import norm_name  # noqa: E402

CONSTELLATIONS_YML = REPO_ROOT / "identity" / "fcc_constellations.yml"

_LATEST_SSAL = """
SELECT orbital_location, satellite_name, call_sign, licensee, administration,
       service, frequency_range, in_orbit_date, grant_type, ingest_run_id
FROM raw_fcc_ssal
WHERE ingest_run_id = (
    SELECT max(r.ingest_run_id) FROM raw_fcc_ssal r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'
)
"""

_CONSTELLATION_INSERT = """
INSERT INTO satellite_fcc_authorization
    (satellite_id, call_sign, satellite_name, licensee, service, frequency_range,
     grant_type, match_tier, match_detail, ingest_run_id)
SELECT s.satellite_id, g.call_sign, g.satellite_name, g.licensee, g.service,
       g.frequency_range, g.grant_type, 'constellation',
       %(detail)s, g.ingest_run_id
FROM ({latest}) g
JOIN satellite s ON s.canonical_name ILIKE %(catalog_prefix)s
WHERE g.satellite_name ILIKE %(ssal_like)s
  AND g.call_sign IS NOT NULL AND g.frequency_range IS NOT NULL
ON CONFLICT (satellite_id, call_sign, frequency_range) DO NOTHING
""".format(latest=_LATEST_SSAL)


def build(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM satellite_fcc_authorization")

        # Tier 1: curated blanket-license constellations.
        spec = yaml.safe_load(CONSTELLATIONS_YML.read_text(encoding="utf-8"))
        tier1 = 0
        for entry in spec.get("constellations", []):
            cur.execute(
                _CONSTELLATION_INSERT,
                {
                    "ssal_like": entry["ssal_like"],
                    "catalog_prefix": entry["catalog_prefix"],
                    "detail": f"blanket license mapping {entry['ssal_like']}"
                              f" -> {entry['catalog_prefix']}",
                },
            )
            tier1 += cur.rowcount

        # Tier 2: GSO names, unique in both directions. Python-side because the uniqueness
        # bookkeeping over ~hundreds of rows is clearer than a four-way SQL join, and the row
        # count makes performance irrelevant.
        cur.execute(_LATEST_SSAL)
        cols = [d.name for d in cur.description]
        grants = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute(
            "SELECT satellite_id, canonical_name FROM satellite "
            "WHERE canonical_name IS NOT NULL AND object_type = 'PAYLOAD'"
        )
        by_key: dict[str, list[int]] = {}
        for sid, name in cur.fetchall():
            by_key.setdefault(norm_name(name), []).append(sid)

        grant_keys: dict[str, int] = {}
        for g in grants:
            if g["satellite_name"]:
                key = norm_name(g["satellite_name"])
                grant_keys[key] = grant_keys.get(key, 0) + 1

        tier2 = 0
        unmatched = 0
        for g in grants:
            if not (g["satellite_name"] and g["call_sign"] and g["frequency_range"]):
                continue
            key = norm_name(g["satellite_name"])
            sids = by_key.get(key, [])
            if len(sids) == 1 and grant_keys.get(key) is not None:
                cur.execute(
                    "INSERT INTO satellite_fcc_authorization "
                    "(satellite_id, call_sign, satellite_name, licensee, service, "
                    " frequency_range, grant_type, match_tier, match_detail, ingest_run_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, 'gso_name', %s, %s) "
                    "ON CONFLICT (satellite_id, call_sign, frequency_range) DO NOTHING",
                    (
                        sids[0], g["call_sign"], g["satellite_name"], g["licensee"],
                        g["service"], g["frequency_range"], g["grant_type"],
                        f"unique name match '{g['satellite_name']}'", g["ingest_run_id"],
                    ),
                )
                tier2 += cur.rowcount
            else:
                unmatched += 1

        cur.execute(
            "SELECT count(*), count(DISTINCT satellite_id) FROM satellite_fcc_authorization"
        )
        total_rows, satellites = cur.fetchone()
    return {
        "grant_rows": len(grants),
        "tier1_constellation_rows": tier1,
        "tier2_gso_name_rows": tier2,
        "unplaced_grant_rows": unmatched,
        "authorization_rows": total_rows,
        "satellites_with_fcc": satellites,
    }


def main() -> None:
    conn = get_conn()
    try:
        stats = build(conn)
        conn.commit()
    finally:
        conn.close()
    print("=== rf authorization build summary ===")
    for k, v in stats.items():
        print(f"{k:28} {v}")


if __name__ == "__main__":
    main()
