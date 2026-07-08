"""Data-driven operator enrichment — the fix for ~4% operator coverage.

Ownership in the raw catalogs is expressed as org CODES (GCAT `SPXS`, SATCAT `ITSO`), while the
hand-curated seed lists only ~17 operators by human name, so the resolver's alias lookup misses
almost everything. This module closes the gap: every GCAT org that actually appears as an Owner
code (plus every documented SATCAT owner code) becomes an operator row, with the code attached as
an `operator_alias`. Because the resolver matches an owner assertion's raw value (the code) against
`operator_alias.alias`, aliasing every code is exactly what lights up coverage.

Guarantees:
  * **Seed wins.** A code whose org belongs to a curated seed operator (by seed `gcat_codes`, or the
    org's Name/EName/ShortName case-insensitively matching a seed canonical name/alias) attaches to
    that seed operator instead of spawning a duplicate — so `SPXS` lands on the seed's SpaceX and the
    M&A relationships keep working.
  * **Idempotent.** Re-runs create no duplicate operators/aliases/relationships (the whole build is
    re-runnable). Enriched GCAT operators are keyed by their code alias; the seed-vs-enrich decision
    is short-circuited on re-runs.
  * **Name collisions are handled, not crashed.** `operator.canonical_name` is UNIQUE and different
    codes can share a Name (renamed orgs) — a collision disambiguates to `Name (CODE)`.

No commit — the caller (scripts/build_graph.py) owns the transaction.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from identity.normalize import norm_name, parse_date_loose

# operator_relationship.valid_from is part of the PRIMARY KEY, so Postgres makes it implicitly
# NOT NULL — a genuine "unknown start" can't be stored as NULL. GCAT-parent subsidiary edges are
# informational (the SCD2 resolver reads only acquired_by/merged_into), so a date-less edge gets this
# sentinel start rather than being dropped; a real TStart is used whenever it parses.
_UNKNOWN_START = dt.date(1, 1, 1)

_OPERATOR_SEED = Path(__file__).with_name("operator_seed.yml")
_SATCAT_CODES = Path(__file__).with_name("satcat_owner_codes.yml")

# GCAT orgs `Class` -> canonical operator_class. Verified against the live orgs.tsv by sampling names
# per class: A = academic (universities/institutes), B = business/commercial company, C = civil
# (government/space-agency/state), D = defense/military. Only these four appear; any other value ->
# NULL (conservative, never guess).
_CLASS_BY_ORG_CLASS = {
    "A": "academic",
    "B": "commercial",
    "C": "civil",
    "D": "defense",
}


def _operator_class(org_type, org_class) -> str | None:
    """Map GCAT org Class to a canonical operator_class, conservatively (unknown -> None)."""
    if org_class:
        return _CLASS_BY_ORG_CLASS.get(org_class.strip().upper())
    return None


# --- source loading -----------------------------------------------------------


def _latest_run(conn, table: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT max(r.ingest_run_id) FROM {table} r "
            "JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'"
        )
        return cur.fetchone()[0]


def _load_orgs(conn) -> dict[str, dict]:
    """{code: org fields} from the latest OK raw_gcat_orgs snapshot."""
    run = _latest_run(conn, "raw_gcat_orgs")
    if run is None:
        return {}
    out: dict[str, dict] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT code, state_code, org_type, org_class, t_start, t_stop, short_name, "
            "name, e_name, parent_code FROM raw_gcat_orgs WHERE ingest_run_id = %s",
            (run,),
        )
        for row in cur.fetchall():
            out[row[0]] = {
                "code": row[0], "state_code": row[1], "org_type": row[2], "org_class": row[3],
                "t_start": row[4], "t_stop": row[5], "short_name": row[6], "name": row[7],
                "e_name": row[8], "parent_code": row[9],
            }
    return out


def _distinct_owner_codes(conn, table: str) -> set[str]:
    run = _latest_run(conn, table)
    if run is None:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT owner FROM {table} WHERE ingest_run_id = %s AND owner IS NOT NULL",
            (run,),
        )
        return {r[0] for r in cur.fetchall()}


def _op_id_by_canonical(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT canonical_name, operator_id FROM operator")
        return {name: oid for name, oid in cur.fetchall()}


def _nkey(s) -> str:
    """Normalized name-match key. norm_name casefolds, drops bracketed suffixes (`SpaceX (Seattle)`
    -> `spacex`) and collapses punctuation/whitespace — this is what makes `SPXS` land on the seed's
    SpaceX. Returns '' (never matched) for empty/None."""
    return norm_name(s) if s else ""


def _all_names_cf(conn) -> dict[str, int]:
    """normalized(name) -> operator_id across canonical names + every alias (first row wins)."""
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT canonical_name, operator_id FROM operator")
        for name, oid in cur.fetchall():
            key = _nkey(name)
            if key:
                out.setdefault(key, oid)
        cur.execute("SELECT alias, operator_id FROM operator_alias")
        for alias, oid in cur.fetchall():
            key = _nkey(alias)
            if key:
                out.setdefault(key, oid)
    return out


def _load_seed(path, op_by_name: dict[str, int]) -> dict:
    """Resolve the curated seed into {gcat_code_cf/satcat_code_cf/name_cf -> operator_id} lookups.

    Only operators that actually exist in the DB (seeded just before enrichment) are included.
    """
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    gcat_code_cf: dict[str, int] = {}
    satcat_code_cf: dict[str, int] = {}
    name_cf: dict[str, int] = {}
    for op in doc.get("operators", []):
        oid = op_by_name.get(op["name"])
        if oid is None:
            continue
        for nm in [op["name"], *op.get("aliases", [])]:
            key = _nkey(nm)
            if key:
                name_cf.setdefault(key, oid)
        for c in op.get("gcat_codes", []):
            gcat_code_cf.setdefault(c.strip().casefold(), oid)
        for c in op.get("satcat_codes", []):
            satcat_code_cf.setdefault(c.strip().casefold(), oid)
    return {"gcat_code_cf": gcat_code_cf, "satcat_code_cf": satcat_code_cf, "name_cf": name_cf}


def _load_satcat_codes(path) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open() as fh:
        doc = yaml.safe_load(fh) or {}
    return {str(k): v for k, v in (doc.get("codes") or {}).items() if v}


# --- helpers ------------------------------------------------------------------


def _display_name(org: dict) -> str:
    """orgs.tsv Name, but EName when Name is non-ASCII and an EName exists; fall back to the code."""
    name = org.get("name")
    if name and not name.isascii() and org.get("e_name"):
        name = org["e_name"]
    if not name:
        name = org.get("e_name") or org.get("short_name") or org["code"]
    return name


def _attach_alias(cur, operator_id: int, alias: str, source: str) -> None:
    cur.execute(
        "INSERT INTO operator_alias (operator_id, alias, source) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (operator_id, alias, source),
    )


def _attach_name_variants(cur, operator_id: int, org: dict, source: str, all_codes: set[str]) -> None:
    """Attach ShortName/Name/EName as aliases — but never a value that is itself a GCAT code, so the
    code alias stays an unambiguous identity key (a name-variant equal to some other org's code
    could otherwise point one code alias at two operators)."""
    for value in (org.get("short_name"), org.get("name"), org.get("e_name")):
        if value and value not in all_codes:
            _attach_alias(cur, operator_id, value, source)


def _insert_operator(cur, desired: str, code: str, country, opclass, op_by_name: dict[str, int]) -> int:
    """Create (or fetch) an operator with a UNIQUE canonical_name, disambiguating a collision with a
    DIFFERENT operator as `desired (CODE)`. Idempotent by canonical_name."""
    name = desired if desired not in op_by_name else f"{desired} ({code})"
    cur.execute(
        "INSERT INTO operator (canonical_name, country, operator_class) VALUES (%s, %s, %s) "
        "ON CONFLICT (canonical_name) DO NOTHING RETURNING operator_id",
        (name, country, opclass),
    )
    row = cur.fetchone()
    if row is None:  # name already present (a prior run) -> fetch it
        cur.execute("SELECT operator_id FROM operator WHERE canonical_name = %s", (name,))
        row = cur.fetchone()
    op_by_name[name] = row[0]
    return row[0]


def _upsert_relationship(cur, child_id: int, parent_id: int, org: dict, source: str) -> bool:
    """One `subsidiary_of` edge per (child, parent, source). Guarded by NOT EXISTS rather than
    ON CONFLICT because valid_from is nullable (NULLs are distinct in the PK, so ON CONFLICT would
    let re-runs duplicate a NULL-dated edge)."""
    valid_from = parse_date_loose(org.get("t_start")) or _UNKNOWN_START
    valid_to = parse_date_loose(org.get("t_stop"))
    cur.execute(
        "INSERT INTO operator_relationship "
        "(child_id, parent_id, relationship, valid_from, valid_to, source) "
        "SELECT %s, %s, 'subsidiary_of', %s, %s, %s WHERE NOT EXISTS ("
        "  SELECT 1 FROM operator_relationship WHERE child_id = %s AND parent_id = %s "
        "  AND relationship = 'subsidiary_of' AND source = %s)",
        (child_id, parent_id, valid_from, valid_to, source, child_id, parent_id, source),
    )
    return cur.rowcount > 0


def _seed_target_gcat(code: str, org: dict, seed: dict) -> int | None:
    """The seed operator this GCAT code belongs to, or None. Seed wins by code or by org name."""
    hit = seed["gcat_code_cf"].get(code.strip().casefold())
    if hit is not None:
        return hit
    for nm in (org.get("name"), org.get("e_name"), org.get("short_name")):
        key = _nkey(nm)
        if key:
            hit = seed["name_cf"].get(key)
            if hit is not None:
                return hit
    return None


# --- entry point --------------------------------------------------------------


def enrich(conn, operator_seed_path=_OPERATOR_SEED, satcat_codes_path=_SATCAT_CODES) -> dict:
    """Enrich operators from GCAT orgs + SATCAT owner codes. Idempotent; returns build stats."""
    orgs = _load_orgs(conn)
    op_by_name = _op_id_by_canonical(conn)
    seed = _load_seed(operator_seed_path, op_by_name)
    all_codes = set(orgs)

    stats = {
        "gcat_codes_used": 0, "gcat_codes_no_org": 0, "gcat_seed_attached": 0,
        "gcat_operators_created": 0, "operators_created": 0,
        "relationships_added": 0, "satcat_codes_used": 0, "satcat_codes_no_doc": 0,
        "satcat_operators_created": 0,
    }

    with conn.cursor() as cur:
        # Prior-run code aliases (identity keys) so re-runs short-circuit without re-deciding/creating.
        cur.execute("SELECT alias, operator_id FROM operator_alias WHERE source = 'gcat_orgs'")
        existing_gcat = {a: oid for a, oid in cur.fetchall() if a in all_codes}

        # --- GCAT org enrichment ---
        gcat_codes = _distinct_owner_codes(conn, "raw_gcat_satcat")
        code_to_opid: dict[str, int] = {}
        for code in sorted(gcat_codes):
            org = orgs.get(code)
            if org is None:
                stats["gcat_codes_no_org"] += 1
                continue
            stats["gcat_codes_used"] += 1
            if code in existing_gcat:  # already enriched/attached on a prior run
                code_to_opid[code] = existing_gcat[code]
                continue
            target = _seed_target_gcat(code, org, seed)
            if target is not None:
                stats["gcat_seed_attached"] += 1
            else:
                before = len(op_by_name)
                target = _insert_operator(
                    cur, _display_name(org), code, org.get("state_code"),
                    _operator_class(org.get("org_type"), org.get("org_class")), op_by_name,
                )
                if len(op_by_name) > before:
                    stats["gcat_operators_created"] += 1
                    stats["operators_created"] += 1
            _attach_alias(cur, target, code, "gcat_orgs")
            _attach_name_variants(cur, target, org, "gcat_orgs", all_codes)
            existing_gcat[code] = target
            code_to_opid[code] = target

        # --- GCAT parent -> subsidiary_of relationships (both endpoints must be operators) ---
        for code, child_opid in code_to_opid.items():
            org = orgs.get(code)
            parent = org.get("parent_code") if org else None
            if not parent:
                continue
            parent_opid = code_to_opid.get(parent)
            if parent_opid is None or parent_opid == child_opid:
                continue
            if _upsert_relationship(cur, child_opid, parent_opid, org, "gcat_orgs"):
                stats["relationships_added"] += 1

        # --- SATCAT owner codes ---
        satcat_map = _load_satcat_codes(satcat_codes_path)
        names_cf = _all_names_cf(conn)  # seed + freshly-enriched GCAT operators (+ any existing)
        for code in sorted(_distinct_owner_codes(conn, "raw_satcat")):
            org_name = satcat_map.get(code)
            if not org_name:
                stats["satcat_codes_no_doc"] += 1
                continue
            stats["satcat_codes_used"] += 1
            key = _nkey(org_name)
            target = seed["satcat_code_cf"].get(code.strip().casefold())
            if target is None and key:
                target = names_cf.get(key)  # seed name / GCAT-org name / previously-created
            if target is None:
                before = len(op_by_name)
                target = _insert_operator(cur, org_name, code, None, None, op_by_name)
                if key:
                    names_cf.setdefault(key, target)
                if len(op_by_name) > before:
                    stats["satcat_operators_created"] += 1
                    stats["operators_created"] += 1
            _attach_alias(cur, target, code, "satcat_sources")

    return stats
