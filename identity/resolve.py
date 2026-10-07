"""Assertion -> dimension resolver. Precedence is config (precedence.yml), not code.

For each satellite and attribute the resolver applies the per-attribute source precedence and
writes the winner to the dimension tables; losing claims stay queryable in the claim table
("disagreements are data, not errors"). Status resolves through the status_mapping table with a
fall-through on UNKNOWN, and owners resolve to operators with SCD2 temporal ownership (the
OneWeb->Eutelsat split). No commit — the caller owns the transaction.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml

from identity.normalize import canonical_object_type, parse_date_loose

_PRECEDENCE_DEFAULT = Path(__file__).with_name("precedence.yml")
_DISPLAY_NAMES_DEFAULT = Path(__file__).with_name("display_names.yml")


def load_precedence(path=None) -> dict:
    with open(path or _PRECEDENCE_DEFAULT) as fh:
        return yaml.safe_load(fh)


def _assertions(conn, attribute):
    """Return {satellite_id: {source: (value, observed_to)}} for one attribute: each source's
    current claim per satellite (v_current_claim, migration 0023), the claim reached through a
    key that currently identifies the satellite, last observed when the feed was.

    Until 2026-10-05 this fetched every retained run's copy of every claim (7.4M rows per
    attribute) and kept the last by dict overwrite: the nightly's swap storm. A satellite with
    two keys for one source (merged objects) has two current claims; the greater key wins, as
    the old tie-break had it.
    """
    out: dict[int, dict[str, tuple]] = defaultdict(dict)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (satellite_id, source) satellite_id, source, value, observed_to "
            "FROM v_current_claim WHERE attribute = %s "
            "ORDER BY satellite_id, source, source_key DESC",
            (attribute,),
        )
        for sat_id, source, value, observed_to in cur.fetchall():
            out[sat_id][source] = (value, observed_to)
    return out


def _pick(by_source: dict, order: list[str]):
    """First (source, value, observed_at) present in precedence order, else None."""
    for src in order:
        if src in by_source:
            value, observed = by_source[src]
            return src, value, observed
    return None


# --- scalar attributes --------------------------------------------------------


def _resolve_name(conn, order) -> None:
    data = _assertions(conn, "name")
    with conn.cursor() as cur:
        for sat_id, by_source in data.items():
            picked = _pick(by_source, order)
            if picked and picked[1]:
                cur.execute(
                    "UPDATE satellite SET canonical_name = %s, updated_at = now() "
                    "WHERE satellite_id = %s",
                    (picked[1], sat_id),
                )


def _apply_display_name_overrides(conn, path=None) -> int:
    """Curated display names, applied last so they win over any precedence outcome.

    GCAT names some famous spacecraft by article number or pre-launch designation (the ISS is
    '77KM No. 175-01', NOAA-19 is "NOAA N'"), which the name precedence then faithfully serves
    to users. The overrides in display_names.yml are keyed by NORAD id and each records the
    name it replaces, so the curation is auditable; the underlying assertions stay untouched.
    Returns the number of rows updated this run.
    """
    with open(path or _DISPLAY_NAMES_DEFAULT) as fh:
        spec = yaml.safe_load(fh) or {}
    applied = 0
    with conn.cursor() as cur:
        for entry in spec.get("display_names", []):
            cur.execute(
                "UPDATE satellite SET canonical_name = %(name)s, updated_at = now() "
                "WHERE norad_id = %(norad)s AND canonical_name IS DISTINCT FROM %(name)s",
                {"name": entry["name"], "norad": entry["norad_id"]},
            )
            applied += cur.rowcount
    return applied


def _resolve_object_type(conn, order) -> None:
    data = _assertions(conn, "object_type")
    with conn.cursor() as cur:
        for sat_id, by_source in data.items():
            picked = _pick(by_source, order)
            if picked:
                cur.execute(
                    "UPDATE satellite SET object_type = %s, updated_at = now() "
                    "WHERE satellite_id = %s",
                    (canonical_object_type(picked[1]), sat_id),
                )


def _gcat_ddate_is_decay(conn) -> dict[int, bool]:
    """Per satellite, whether GCAT's DDate on its decay claim is an end of life. GCAT's DDate
    is the time a phase ended and Status names the event that ended it: for a reentry or
    landing (status_map.yml maps it to DECAYED) that is the decay date, but for a grapple,
    docking, attachment or renaming it is that event's date, and resolving it as a decay put
    1998-12-06 (Zarya's grapple by Unity, GCAT status GRP) on the ISS card until 2026-10-07.
    The status is the one on the same GCAT key as the decay claim _assertions picks (the
    greatest key with one), since a satellite holding two GCAT keys can hold two phases."""
    mapping = _status_mapping(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (si.satellite_id) si.satellite_id, st.value
            FROM claim d
            JOIN claim st ON st.source = 'gcat' AND st.source_key = d.source_key
                         AND st.attribute = 'status' AND st.closed_run IS NULL
            JOIN satellite_identifier si
              ON si.source = 'gcat' AND si.id_type = 'gcat_id' AND si.id_value = d.source_key
             AND si.valid_to IS NULL
            WHERE d.source = 'gcat' AND d.attribute = 'decay_date' AND d.closed_run IS NULL
            ORDER BY si.satellite_id, d.source_key DESC
            """
        )
        return {sat: _canonical(mapping, "gcat", status) == "DECAYED"
                for sat, status in cur.fetchall()}


def _resolve_decay_date(conn, order) -> None:
    data = _assertions(conn, "decay_date")
    is_decay = _gcat_ddate_is_decay(conn)
    phase_dates = [sat for sat, by_source in data.items()
                   if "gcat" in by_source and not is_decay.get(sat, False)]
    for sat in phase_dates:
        del data[sat]["gcat"]
        if not data[sat]:
            del data[sat]
    with conn.cursor() as cur:
        for sat_id, by_source in data.items():
            picked = _pick(by_source, order)
            if not picked:
                continue
            d = parse_date_loose(picked[1])
            if d is not None:
                cur.execute(
                    "UPDATE satellite SET decay_date = %s, updated_at = now() "
                    "WHERE satellite_id = %s",
                    (d, sat_id),
                )
        # Retraction: a date resolved earlier from a claim no source currently makes for this
        # satellite (its only decay claim came through a since-retired sibling key: one case on
        # production, 2026-10-06; or a GCAT phase date that is not a decay) would otherwise
        # stay on the card forever.
        cur.execute(
            "UPDATE satellite SET decay_date = NULL, updated_at = now() "
            "WHERE decay_date IS NOT NULL AND NOT (satellite_id = ANY(%s))",
            (list(data.keys()),),
        )


# --- status -------------------------------------------------------------------


def _status_mapping(conn) -> dict[tuple[str, str], str]:
    with conn.cursor() as cur:
        cur.execute("SELECT source, source_value, canonical_status FROM status_mapping")
        return {(s, v): c for s, v, c in cur.fetchall()}


def _canonical(mapping: dict, src: str, value: str) -> str | None:
    """The canonical status for a source value, or None when unmapped. GCAT marks an uncertain
    phase with a trailing "?" (R? = reentered, probably; 13 objects on 2026-10-07): the phase
    is the same, so the lookup retries without it."""
    canonical = mapping.get((src, value))
    if canonical is None and src == "gcat" and value.endswith("?"):
        canonical = mapping.get((src, value.rstrip("?").strip()))
    return canonical


def _resolve_status(conn, order, stats) -> None:
    """Resolve canonical status, falling through UNKNOWN so GCAT's physical phase yields to
    SATCAT's operational code; unmapped source values resolve to UNKNOWN and are counted."""
    mapping = _status_mapping(conn)
    data = _assertions(conn, "status")
    unmapped = set()
    resolved = 0
    with conn.cursor() as cur:
        for sat_id, by_source in data.items():
            winner = None
            for src in order:
                if src not in by_source:
                    continue
                value, observed = by_source[src]
                canonical = _canonical(mapping, src, value)
                if canonical is None:
                    unmapped.add((src, value))
                    continue  # unmapped -> UNKNOWN, keep looking
                if canonical == "UNKNOWN":
                    continue  # fall through to the next source
                winner = (canonical, src, observed)
                break
            if winner is None:
                continue  # everything UNKNOWN/unmapped -> leave status unresolved
            canonical, src, observed = winner
            cur.execute(
                "INSERT INTO satellite_status_history (satellite_id, canonical_status, "
                "observed_at, source) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (sat_id, canonical, observed, src),
            )
            resolved += 1
    stats["unmapped_status"] = sorted(unmapped)
    stats["status_resolved"] = resolved


# --- owner -> operator (SCD2) -------------------------------------------------


def _alias_map(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT canonical_name, operator_id FROM operator")
        for name, oid in cur.fetchall():
            out[name.casefold()] = oid
        cur.execute("SELECT alias, operator_id FROM operator_alias")
        for alias, oid in cur.fetchall():
            out.setdefault(alias.casefold(), oid)
    return out


def _relationship_map(conn) -> dict[int, tuple[int, object]]:
    """child operator_id -> (parent operator_id, valid_from) for acquisitions/mergers."""
    out: dict[int, tuple[int, object]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT child_id, parent_id, valid_from FROM operator_relationship "
            "WHERE relationship IN ('acquired_by', 'merged_into') ORDER BY valid_from"
        )
        for child, parent, valid_from in cur.fetchall():
            out.setdefault(child, (parent, valid_from))  # earliest valid_from wins
    return out


def _launch_dates(conn) -> dict[int, object]:
    with conn.cursor() as cur:
        cur.execute("SELECT satellite_id, launch_date FROM satellite")
        return {sid: ld for sid, ld in cur.fetchall()}


def _resolve_owner(conn, order, stats) -> None:
    aliases = _alias_map(conn)
    rel = _relationship_map(conn)
    launches = _launch_dates(conn)
    data = _assertions(conn, "owner")
    unmatched = set()
    resolved = 0
    with conn.cursor() as cur:
        for sat_id, by_source in data.items():
            operator_id = None
            fallback_value = None
            for src in order:
                if src not in by_source:
                    continue
                value = by_source[src][0]
                if fallback_value is None:
                    fallback_value = value
                oid = aliases.get(value.strip().casefold())
                if oid is not None:
                    operator_id = oid
                    break
            if operator_id is None:
                if fallback_value is not None:
                    unmatched.add(fallback_value)
                continue
            if _write_owner(cur, sat_id, operator_id, launches.get(sat_id), rel):
                resolved += 1
    stats["unmatched_owners"] = sorted(unmatched)
    stats["operator_resolved"] = resolved


def _write_owner(cur, sat_id, operator_id, launch, rel) -> bool:
    cur.execute(
        "DELETE FROM satellite_operator WHERE satellite_id = %s AND source = 'resolve' "
        "AND role = 'owner'",
        (sat_id,),
    )
    if launch is None:
        return False  # cannot time-bound ownership without a launch date
    if operator_id in rel and launch < rel[operator_id][1]:
        parent_id, split = rel[operator_id]
        _insert_owner(cur, sat_id, operator_id, launch, split)
        _insert_owner(cur, sat_id, parent_id, split, None)
    else:
        _insert_owner(cur, sat_id, operator_id, launch, None)
    return True


def _insert_owner(cur, sat_id, operator_id, valid_from, valid_to) -> None:
    cur.execute(
        """
        INSERT INTO satellite_operator
            (satellite_id, operator_id, role, valid_from, valid_to, source, confidence)
        VALUES (%s, %s, 'owner', %s, %s, 'resolve', 1.00)
        ON CONFLICT (satellite_id, operator_id, role, valid_from) DO NOTHING
        """,
        (sat_id, operator_id, valid_from, valid_to),
    )


# --- entry point --------------------------------------------------------------


def resolve(conn, precedence_path=None) -> dict:
    """Resolve every attribute for every matched satellite. Returns coverage/DQ stats."""
    prec = load_precedence(precedence_path)
    stats: dict = {}
    _resolve_name(conn, prec["name"])
    _resolve_object_type(conn, prec["object_type"])
    _resolve_decay_date(conn, prec["decay_date"])
    _resolve_status(conn, prec["status"], stats)
    _resolve_owner(conn, prec["owner"], stats)
    stats["display_name_overrides"] = _apply_display_name_overrides(conn)
    return stats
