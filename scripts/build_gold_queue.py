"""Stratified, deterministic selection of hard identity-resolution cases into gold_case.

The gold set is the ground-truth program: ~200 hard cases the repo owner hand-arbitrates so we can
quote an honest identity-resolution error rate per failure mode. This script does the *selection*
(and captures the system's current answer + full evidence for each case); review.py does the
labeling; score_gold.py turns labels into per-stratum accuracy.

Design guarantees:
  - Deterministic. Every selector orders by stable keys (norad, jcat, cospar, similarity); there is
    no randomness anywhere. Re-running on the same data selects the same cases in the same order.
  - Idempotent + verdict-safe. Cases upsert on (case_type, subject_ref): a re-run REFRESHES the
    system_answer/evidence (the graph may have changed) but NEVER touches a human verdict. The only
    columns a re-run can overwrite are satellite_id/question/system_answer/evidence.
  - Read-only against the graph; the sole write is the gold_case upsert.

Strata (see docs/GOLD.md for the rationale of each):
  a ambiguous_cospar     b rideshare_orphan   c missed_join_candidate   d owner_dispute
  e status_conflict      f decay_conflict     g type_conflict           h stale_owner
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.db import get_conn
from identity.normalize import canonical_object_type, norm_name, orbital_regime, parse_date_loose

# Target sizes per stratum. "all" strata take every qualifying case; sampled strata take the top-N
# by the stratum's ordering key. Live counts will differ from these planning estimates and that is
# expected — the honest counts are what build prints and what score_gold reports.
TARGET_OWNER_DISPUTE = 30
TARGET_MISSED_JOIN = 30
TARGET_DECAY = 20
TARGET_TYPE = 25
TARGET_STALE = 15

MISSED_JOIN_SIM_THRESHOLD = 0.75
MISSED_JOIN_LAUNCH_WINDOW_DAYS = 30

STRATUM_ORDER = [
    "ambiguous_cospar",
    "rideshare_orphan",
    "missed_join_candidate",
    "owner_dispute",
    "status_conflict",
    "decay_conflict",
    "type_conflict",
    "stale_owner",
]


# ---------------------------------------------------------------------------------------------
# Evidence assembly (shared by every stratum)
# ---------------------------------------------------------------------------------------------


def _iso(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def _perigee_apogee(cur, norad_id, jcat):
    """Latest (perigee_km, apogee_km) for a satellite: SATCAT snapshot first, else GCAT."""
    if norad_id is not None:
        cur.execute(
            "SELECT perigee, apogee FROM raw_satcat WHERE norad_cat_id = %s "
            "ORDER BY ingest_run_id DESC LIMIT 1",
            (norad_id,),
        )
        row = cur.fetchone()
        if row and (row[0] is not None or row[1] is not None):
            return (row[0], row[1])
    if jcat is not None:
        cur.execute(
            "SELECT perigee_km, apogee_km FROM raw_gcat_satcat WHERE jcat = %s "
            "ORDER BY ingest_run_id DESC LIMIT 1",
            (jcat,),
        )
        row = cur.fetchone()
        if row:
            return (row[0], row[1])
    return (None, None)


def _identifiers(cur, satellite_id):
    cur.execute(
        "SELECT id_type, id_value, source, confidence FROM satellite_identifier "
        "WHERE satellite_id = %s ORDER BY id_type, source, id_value",
        (satellite_id,),
    )
    return [
        {"id_type": t, "id_value": v, "source": s, "confidence": float(c)}
        for t, v, s, c in cur.fetchall()
    ]


def _assertions(cur, satellite_id):
    cur.execute(
        "SELECT attribute, value, source, observed_at FROM source_assertion "
        "WHERE satellite_id = %s ORDER BY attribute, source, observed_at DESC, ingest_run_id DESC",
        (satellite_id,),
    )
    return [
        {"attribute": a, "value": v, "source": s, "observed_at": _iso(o)}
        for a, v, s, o in cur.fetchall()
    ]


def _resolved(cur, satellite_id):
    """The graph's current resolved status + owner history for a satellite."""
    cur.execute(
        "SELECT canonical_status, observed_at, source FROM satellite_status_history "
        "WHERE satellite_id = %s ORDER BY observed_at DESC, source LIMIT 1",
        (satellite_id,),
    )
    st = cur.fetchone()
    cur.execute(
        "SELECT o.canonical_name, so.role, so.valid_from, so.valid_to, so.source "
        "FROM satellite_operator so JOIN operator o ON o.operator_id = so.operator_id "
        "WHERE so.satellite_id = %s AND so.role = 'owner' "
        "ORDER BY so.valid_from, o.canonical_name",
        (satellite_id,),
    )
    owner_history = [
        {
            "operator": name,
            "role": role,
            "valid_from": _iso(vf),
            "valid_to": _iso(vt),
            "source": src,
        }
        for name, role, vf, vt, src in cur.fetchall()
    ]
    current_owner = next((o["operator"] for o in owner_history if o["valid_to"] is None), None)
    return {
        "status": st[0] if st else None,
        "status_source": st[2] if st else None,
        "owner": current_owner,
        "owner_history": owner_history,
    }


def satellite_evidence(cur, satellite_id):
    """Full evidence packet for one satellite: identity, every assertion, dates, regime, resolved."""
    cur.execute(
        "SELECT norad_id, cospar_id, canonical_name, object_type, launch_date, decay_date "
        "FROM satellite WHERE satellite_id = %s",
        (satellite_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {"satellite_id": satellite_id, "error": "satellite not found"}
    norad, cospar, name, obj_type, launch, decay = row
    identifiers = _identifiers(cur, satellite_id)
    jcat = next((i["id_value"] for i in identifiers if i["id_type"] == "gcat_id"), None)
    perigee, apogee = _perigee_apogee(cur, norad, jcat)
    return {
        "satellite_id": satellite_id,
        "norad_id": norad,
        "cospar_id": cospar,
        "jcat": jcat,
        "canonical_name": name,
        "object_type": obj_type,
        "launch_date": _iso(launch),
        "decay_date": _iso(decay),
        "perigee_km": float(perigee) if perigee is not None else None,
        "apogee_km": float(apogee) if apogee is not None else None,
        "orbital_regime": orbital_regime(perigee, apogee),
        "identifiers": identifiers,
        "assertions": _assertions(cur, satellite_id),
        "resolved": _resolved(cur, satellite_id),
    }


def _subject_ref(norad_id, jcat, satellite_id):
    """Stable human key for resume/dedupe: prefer NORAD, then JCAT, then satellite_id."""
    if norad_id is not None:
        return f"norad:{norad_id}"
    if jcat:
        return f"jcat:{jcat}"
    return f"sat:{satellite_id}"


# ---------------------------------------------------------------------------------------------
# Stratum selectors. Each returns a list of case dicts (case_type/satellite_id/subject_ref/
# question/system_answer/evidence), already deterministically ordered and truncated.
# ---------------------------------------------------------------------------------------------


def stratum_ambiguous_cospar(cur):
    """(a) COSPAR designators that map to >1 satellite: legitimate multi-object or a bad merge?"""
    cur.execute(
        "SELECT id_value, array_agg(DISTINCT satellite_id ORDER BY satellite_id) AS sats "
        "FROM satellite_identifier WHERE id_type = 'cospar' "
        "GROUP BY id_value HAVING count(DISTINCT satellite_id) > 1 "
        "ORDER BY id_value"
    )
    cases = []
    for cospar, sat_ids in cur.fetchall():
        sats = [satellite_evidence(cur, sid) for sid in sat_ids]
        names = ", ".join(
            f"{s['canonical_name']} ({_subject_ref(s['norad_id'], s['jcat'], s['satellite_id'])})"
            for s in sats
        )
        cases.append(
            {
                "case_type": "ambiguous_cospar",
                "satellite_id": None,
                "subject_ref": f"cospar:{cospar}",
                "question": (
                    f"COSPAR {cospar} is currently mapped to {len(sats)} distinct satellites: "
                    f"{names}. Is this multi-mapping legitimate (genuinely separate objects that "
                    f"share a designator) or should some of these be merged?"
                ),
                "system_answer": (
                    f"The graph keeps {len(sats)} separate satellites under COSPAR {cospar}: {names}."
                ),
                "evidence": {"cospar": cospar, "n_satellites": len(sats), "satellites": sats},
            }
        )
    return cases


def stratum_rideshare_orphan(cur):
    """(b) GCAT-only payloads with no NORAD (fresh rideshares): distinct object, and who operates?"""
    cur.execute(
        "SELECT s.satellite_id, s.canonical_name, s.cospar_id FROM satellite s "
        "WHERE s.norad_id IS NULL AND s.object_type = 'PAYLOAD' "
        "AND NOT EXISTS (SELECT 1 FROM source_assertion a "
        "                WHERE a.satellite_id = s.satellite_id AND a.source = 'satcat') "
        "ORDER BY s.cospar_id, s.satellite_id"
    )
    rows = cur.fetchall()
    cases = []
    for sid, name, cospar in rows:
        ev = satellite_evidence(cur, sid)
        owner = ev["resolved"]["owner"] or "unresolved"
        gcat_owner = next(
            (a["value"] for a in ev["assertions"] if a["attribute"] == "owner" and a["source"] == "gcat"),
            None,
        )
        cases.append(
            {
                "case_type": "rideshare_orphan",
                "satellite_id": sid,
                "subject_ref": _subject_ref(None, ev["jcat"], sid),
                "question": (
                    f"{name} ({cospar}, GCAT {ev['jcat']}) has no NORAD catalog entry yet. "
                    f"Is it a distinct physical object (not a duplicate or a piece of another), "
                    f"and who operates it?"
                ),
                "system_answer": (
                    f"Treated as a distinct GCAT-only payload; resolved owner: {owner} "
                    f"(GCAT owner code: {gcat_owner})."
                ),
                "evidence": ev,
            }
        )
    return cases


def stratum_missed_join_candidate(cur):
    """(c) GCAT-only objects name-similar to a same-launch-window SATCAT object the matcher missed.

    Framed as a matcher-recall probe: if the deterministic pass had good recall these are rare. For
    every no-NORAD object, compare its normalized name (difflib ratio) to SATCAT-linked satellites
    launched within +/-30 days; keep pairs >= 0.75 and take the top N by similarity.
    """
    cur.execute(
        "SELECT satellite_id, canonical_name, launch_date FROM satellite "
        "WHERE norad_id IS NULL AND launch_date IS NOT NULL ORDER BY satellite_id"
    )
    orphans = cur.fetchall()
    if not orphans:
        return []
    launches = [o[2] for o in orphans]
    lo = min(launches) - dt.timedelta(days=MISSED_JOIN_LAUNCH_WINDOW_DAYS)
    hi = max(launches) + dt.timedelta(days=MISSED_JOIN_LAUNCH_WINDOW_DAYS)
    cur.execute(
        "SELECT satellite_id, canonical_name, launch_date, norad_id FROM satellite "
        "WHERE norad_id IS NOT NULL AND launch_date BETWEEN %s AND %s "
        "ORDER BY satellite_id",
        (lo, hi),
    )
    satcat = [(sid, name, launch, norad, norm_name(name)) for sid, name, launch, norad in cur.fetchall()]

    scored = []
    for oid, oname, oL in orphans:
        on = norm_name(oname)
        if not on:
            continue
        best = None
        for sid, sname, sL, norad, snorm in satcat:
            if abs((sL - oL).days) > MISSED_JOIN_LAUNCH_WINDOW_DAYS:
                continue
            r = difflib.SequenceMatcher(None, on, snorm).ratio()
            if r >= MISSED_JOIN_SIM_THRESHOLD and (best is None or r > best[0] or (r == best[0] and sid < best[1])):
                best = (r, sid, sname, sL, norad, snorm)
        if best:
            scored.append((round(best[0], 4), oid, oname, oL, best))
    # top N by similarity desc, orphan satellite_id asc as deterministic tiebreak
    scored.sort(key=lambda x: (-x[0], x[1]))
    scored = scored[:TARGET_MISSED_JOIN]

    cases = []
    for sim, oid, oname, oL, best in scored:
        _, cand_sid, cand_name, cand_L, cand_norad, cand_norm = best
        ev = satellite_evidence(cur, oid)
        cand_ev = satellite_evidence(cur, cand_sid)
        ev["missed_join"] = {
            "similarity": sim,
            "orphan_norm_name": norm_name(oname),
            "candidate_norm_name": cand_norm,
            "candidate_norad": cand_norad,
            "candidate_name": cand_name,
            "launch_gap_days": abs((cand_L - oL).days),
            "candidate": cand_ev,
        }
        cases.append(
            {
                "case_type": "missed_join_candidate",
                "satellite_id": oid,
                "subject_ref": _subject_ref(None, ev["jcat"], oid),
                "question": (
                    f"GCAT object {oname} ({ev['jcat']}, launch {ev['launch_date']}) was NOT linked "
                    f"to SATCAT {cand_name} (NORAD {cand_norad}, launch {cand_ev['launch_date']}) "
                    f"despite name similarity {sim} and a {abs((cand_L - oL).days)}-day launch gap. "
                    f"Are these the same physical object (a missed join)?"
                ),
                "system_answer": (
                    "Not linked: the deterministic pass found no shared NORAD/COSPAR, so the graph "
                    "treats them as two distinct objects."
                ),
                "evidence": ev,
            }
        )
    return cases


def stratum_owner_dispute(cur):
    """(d) SATCAT and GCAT owner codes resolve to DIFFERENT commercial operators (not a hierarchy)."""
    cur.execute(
        """
        WITH alias_all AS (
            SELECT lower(canonical_name) AS k, operator_id FROM operator
            UNION SELECT lower(alias) AS k, operator_id FROM operator_alias
        ),
        satcat_owner AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, a.value AS owner_raw
            FROM source_assertion a
            WHERE a.source = 'satcat' AND a.attribute = 'owner' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        ),
        gcat_owner AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, a.value AS owner_raw
            FROM source_assertion a
            WHERE a.source = 'gcat' AND a.attribute = 'owner' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        ),
        sat_op AS (
            SELECT so.satellite_id, so.owner_raw, min(aa.operator_id) AS op
            FROM satcat_owner so JOIN alias_all aa ON aa.k = lower(trim(so.owner_raw))
            GROUP BY so.satellite_id, so.owner_raw
        ),
        gc_op AS (
            SELECT go.satellite_id, go.owner_raw, min(aa.operator_id) AS op
            FROM gcat_owner go JOIN alias_all aa ON aa.k = lower(trim(go.owner_raw))
            GROUP BY go.satellite_id, go.owner_raw
        )
        SELECT s.satellite_id, s.norad_id, s.canonical_name,
               sp.owner_raw AS satcat_code, os.canonical_name AS satcat_op,
               gp.owner_raw AS gcat_code,   og.canonical_name AS gcat_op,
               rc.rcs
        FROM sat_op sp
        JOIN gc_op gp ON gp.satellite_id = sp.satellite_id
        JOIN satellite s ON s.satellite_id = sp.satellite_id
        JOIN operator os ON os.operator_id = sp.op
        JOIN operator og ON og.operator_id = gp.op
        LEFT JOIN LATERAL (
            SELECT rcs FROM raw_satcat r WHERE r.norad_cat_id = s.norad_id
            ORDER BY ingest_run_id DESC LIMIT 1
        ) rc ON true
        WHERE sp.op <> gp.op
          AND os.operator_class = 'commercial' AND og.operator_class = 'commercial'
          AND NOT EXISTS (
              SELECT 1 FROM operator_relationship rel
              WHERE (rel.child_id = sp.op AND rel.parent_id = gp.op)
                 OR (rel.child_id = gp.op AND rel.parent_id = sp.op)
          )
        ORDER BY rc.rcs DESC NULLS LAST, s.norad_id
        LIMIT %s
        """,
        (TARGET_OWNER_DISPUTE,),
    )
    cases = []
    for sid, norad, name, satcat_code, satcat_op, gcat_code, gcat_op, _rcs in cur.fetchall():
        ev = satellite_evidence(cur, sid)
        resolved_owner = ev["resolved"]["owner"] or "unresolved"
        cases.append(
            {
                "case_type": "owner_dispute",
                "satellite_id": sid,
                "subject_ref": _subject_ref(norad, ev["jcat"], sid),
                "question": (
                    f"Who is the current owner of {name} (NORAD {norad})? SATCAT owner code "
                    f"'{satcat_code}' resolves to {satcat_op}; GCAT owner code '{gcat_code}' "
                    f"resolves to {gcat_op}."
                ),
                "system_answer": f"Resolved owner: {resolved_owner}.",
                "evidence": {
                    **ev,
                    "owner_dispute": {
                        "satcat_code": satcat_code,
                        "satcat_operator": satcat_op,
                        "gcat_code": gcat_code,
                        "gcat_operator": gcat_op,
                    },
                },
            }
        )
    return cases


def _status_conflict_rows(cur):
    cur.execute(
        """
        WITH satcat AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, a.value AS raw, m.canonical_status
            FROM source_assertion a
            JOIN status_mapping m ON m.source = 'satcat' AND m.source_value = a.value
            WHERE a.source = 'satcat' AND a.attribute = 'status' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        ),
        gcat AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, a.value AS raw, m.canonical_status
            FROM source_assertion a
            JOIN status_mapping m ON m.source = 'gcat' AND m.source_value = a.value
            WHERE a.source = 'gcat' AND a.attribute = 'status' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        )
        SELECT s.satellite_id, s.norad_id, s.canonical_name,
               sc.raw, sc.canonical_status, gc.raw, gc.canonical_status
        FROM satcat sc
        JOIN gcat gc ON gc.satellite_id = sc.satellite_id
        JOIN satellite s ON s.satellite_id = sc.satellite_id
        WHERE sc.canonical_status <> gc.canonical_status
          AND sc.canonical_status <> 'UNKNOWN' AND gc.canonical_status <> 'UNKNOWN'
        ORDER BY s.norad_id NULLS LAST, s.satellite_id
        """
    )
    return cur.fetchall()


def stratum_status_conflict(cur):
    """(e) SATCAT vs GCAT canonical operational-status disagreements: what is the true status?"""
    cases = []
    for sid, norad, name, sc_raw, sc_canon, gc_raw, gc_canon in _status_conflict_rows(cur):
        ev = satellite_evidence(cur, sid)
        resolved_status = ev["resolved"]["status"] or "unresolved"
        cases.append(
            {
                "case_type": "status_conflict",
                "satellite_id": sid,
                "subject_ref": _subject_ref(norad, ev["jcat"], sid),
                "question": (
                    f"What is the true operational status of {name} (NORAD {norad})? "
                    f"SATCAT says '{sc_raw}' ({sc_canon}); GCAT says '{gc_raw}' ({gc_canon})."
                ),
                "system_answer": f"Resolved status: {resolved_status}.",
                "evidence": {
                    **ev,
                    "status_conflict": {
                        "satcat_raw": sc_raw,
                        "satcat_canonical": sc_canon,
                        "gcat_raw": gc_raw,
                        "gcat_canonical": gc_canon,
                    },
                },
            }
        )
    return cases


def stratum_decay_conflict(cur):
    """(f) Largest parsed decay-date disagreements between SATCAT and GCAT: when did it reenter?"""
    cur.execute(
        """
        WITH sd AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, a.value AS v
            FROM source_assertion a
            WHERE a.source = 'satcat' AND a.attribute = 'decay_date' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        ),
        gd AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, a.value AS v
            FROM source_assertion a
            WHERE a.source = 'gcat' AND a.attribute = 'decay_date' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        )
        SELECT s.satellite_id, s.norad_id, s.canonical_name, sd.v, gd.v
        FROM sd JOIN gd ON gd.satellite_id = sd.satellite_id
        JOIN satellite s ON s.satellite_id = sd.satellite_id
        ORDER BY s.satellite_id
        """
    )
    scored = []
    for sid, norad, name, sv, gv in cur.fetchall():
        ds, dg = parse_date_loose(sv), parse_date_loose(gv)
        if ds is None or dg is None or ds == dg:
            continue
        scored.append((abs((ds - dg).days), sid, norad, name, sv, gv, ds, dg))
    scored.sort(key=lambda x: (-x[0], x[1]))
    scored = scored[:TARGET_DECAY]

    cases = []
    for diff, sid, norad, name, sv, gv, ds, dg in scored:
        ev = satellite_evidence(cur, sid)
        cases.append(
            {
                "case_type": "decay_conflict",
                "satellite_id": sid,
                "subject_ref": _subject_ref(norad, ev["jcat"], sid),
                "question": (
                    f"When did {name} (NORAD {norad}) actually reenter? SATCAT: '{sv}' "
                    f"({ds.isoformat()}); GCAT: '{gv}' ({dg.isoformat()}); the two differ by "
                    f"{diff} days."
                ),
                "system_answer": f"Resolved decay date: {ev['decay_date']}.",
                "evidence": {
                    **ev,
                    "decay_conflict": {
                        "satcat_raw": sv,
                        "satcat_parsed": ds.isoformat(),
                        "gcat_raw": gv,
                        "gcat_parsed": dg.isoformat(),
                        "diff_days": diff,
                    },
                },
            }
        )
    return cases


def stratum_type_conflict(cur):
    """(g) SATCAT DEBRIS vs GCAT payload, sampled deterministically across launch decades."""
    cur.execute(
        """
        WITH st AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, a.value AS v
            FROM source_assertion a
            WHERE a.source = 'satcat' AND a.attribute = 'object_type' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        ),
        gt AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, a.value AS v
            FROM source_assertion a
            WHERE a.source = 'gcat' AND a.attribute = 'object_type' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        )
        SELECT s.satellite_id, s.norad_id, s.canonical_name, s.launch_date, st.v, gt.v
        FROM st JOIN gt ON gt.satellite_id = st.satellite_id
        JOIN satellite s ON s.satellite_id = st.satellite_id
        ORDER BY s.satellite_id
        """
    )
    by_decade = defaultdict(list)
    for sid, norad, name, launch, sv, gv in cur.fetchall():
        if canonical_object_type(sv) == "DEBRIS" and canonical_object_type(gv) == "PAYLOAD":
            decade = (launch.year // 10 * 10) if launch else -1
            by_decade[decade].append((sid, norad, name, launch, sv, gv))
    # Deterministic even spread: round-robin across decades (sorted), each decade ordered by norad.
    for decade in by_decade:
        by_decade[decade].sort(key=lambda r: (r[1] is None, r[1], r[0]))
    picked = []
    decades = sorted(by_decade)
    idx = 0
    while len(picked) < TARGET_TYPE and any(idx < len(by_decade[d]) for d in decades):
        for d in decades:
            if idx < len(by_decade[d]):
                picked.append((d, by_decade[d][idx]))
                if len(picked) >= TARGET_TYPE:
                    break
        idx += 1

    cases = []
    for decade, (sid, norad, name, launch, sv, gv) in picked:
        ev = satellite_evidence(cur, sid)
        cases.append(
            {
                "case_type": "type_conflict",
                "satellite_id": sid,
                "subject_ref": _subject_ref(norad, ev["jcat"], sid),
                "question": (
                    f"Is {name} (NORAD {norad}, launched {ev['launch_date']}) a payload or debris? "
                    f"SATCAT classifies it '{sv}' (DEBRIS); GCAT classifies it '{gv}' (PAYLOAD)."
                ),
                "system_answer": f"Resolved object_type: {ev['object_type']}.",
                "evidence": {
                    **ev,
                    "type_conflict": {
                        "satcat_raw": sv,
                        "gcat_raw": gv,
                        "launch_decade": decade if decade >= 0 else None,
                    },
                },
            }
        )
    return cases


def stratum_stale_owner(cur):
    """(h) Post-M&A satellites with an SCD2 ownership split: is child-until-close/parent-after right?"""
    cur.execute(
        """
        SELECT r.child_id, oc.canonical_name AS child, r.parent_id, op.canonical_name AS parent,
               r.relationship, r.valid_from,
               so_c.satellite_id, s.norad_id, s.canonical_name,
               so_c.valid_from AS child_from, so_c.valid_to AS child_to, so_p.valid_from AS parent_from
        FROM operator_relationship r
        JOIN operator oc ON oc.operator_id = r.child_id
        JOIN operator op ON op.operator_id = r.parent_id
        JOIN satellite_operator so_c
            ON so_c.operator_id = r.child_id AND so_c.role = 'owner' AND so_c.valid_to IS NOT NULL
        JOIN satellite_operator so_p
            ON so_p.operator_id = r.parent_id AND so_p.satellite_id = so_c.satellite_id
           AND so_p.role = 'owner' AND so_p.valid_to IS NULL
        JOIN satellite s ON s.satellite_id = so_c.satellite_id
        WHERE r.relationship IN ('acquired_by', 'merged_into')
        ORDER BY r.child_id, s.norad_id NULLS LAST, so_c.satellite_id
        """
    )
    by_deal = defaultdict(list)
    for row in cur.fetchall():
        by_deal[(row[0], row[2])].append(row)
    # Round-robin across deals so every M&A is represented, each deal ordered by norad.
    deals = sorted(by_deal)
    picked = []
    idx = 0
    while len(picked) < TARGET_STALE and any(idx < len(by_deal[d]) for d in deals):
        for d in deals:
            if idx < len(by_deal[d]):
                picked.append(by_deal[d][idx])
                if len(picked) >= TARGET_STALE:
                    break
        idx += 1

    cases = []
    for row in picked:
        (child_id, child, parent_id, parent, relationship, deal_date,
         sid, norad, name, child_from, child_to, parent_from) = row
        ev = satellite_evidence(cur, sid)
        cases.append(
            {
                "case_type": "stale_owner",
                "satellite_id": sid,
                "subject_ref": _subject_ref(norad, ev["jcat"], sid),
                "question": (
                    f"Is the resolver's SCD2 ownership correct for {name} (NORAD {norad})? It splits "
                    f"ownership at the {child} -> {parent} deal ({relationship}, {deal_date}): "
                    f"{child} until {_iso(child_to)}, then {parent} after."
                ),
                "system_answer": (
                    f"{child} [{_iso(child_from)} -> {_iso(child_to)}], then "
                    f"{parent} [{_iso(parent_from)} -> open]."
                ),
                "evidence": {
                    **ev,
                    "stale_owner": {
                        "child": child,
                        "parent": parent,
                        "relationship": relationship,
                        "deal_date": _iso(deal_date),
                        "child_valid_from": _iso(child_from),
                        "child_valid_to": _iso(child_to),
                        "parent_valid_from": _iso(parent_from),
                    },
                },
            }
        )
    return cases


SELECTORS = {
    "ambiguous_cospar": stratum_ambiguous_cospar,
    "rideshare_orphan": stratum_rideshare_orphan,
    "missed_join_candidate": stratum_missed_join_candidate,
    "owner_dispute": stratum_owner_dispute,
    "status_conflict": stratum_status_conflict,
    "decay_conflict": stratum_decay_conflict,
    "type_conflict": stratum_type_conflict,
    "stale_owner": stratum_stale_owner,
}


# ---------------------------------------------------------------------------------------------
# Upsert (verdict-safe) + driver
# ---------------------------------------------------------------------------------------------


def upsert_case(conn, case) -> None:
    """Insert or refresh one gold_case. On conflict (case_type, subject_ref) refresh the system's
    answer/evidence only; the human verdict columns are deliberately left untouched."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gold_case
                (case_type, satellite_id, subject_ref, question, system_answer, evidence)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (case_type, subject_ref) DO UPDATE SET
                satellite_id  = EXCLUDED.satellite_id,
                question      = EXCLUDED.question,
                system_answer = EXCLUDED.system_answer,
                evidence      = EXCLUDED.evidence
            """,
            (
                case["case_type"],
                case["satellite_id"],
                case["subject_ref"],
                case["question"],
                case["system_answer"],
                json.dumps(case["evidence"], default=str),
            ),
        )


def build(conn, only_type=None) -> dict:
    """Run the selectors and upsert every case. Returns {case_type: count}. Caller commits."""
    counts = {}
    with conn.cursor() as cur:
        for case_type in STRATUM_ORDER:
            if only_type and case_type != only_type:
                continue
            cases = SELECTORS[case_type](cur)
            for case in cases:
                upsert_case(conn, case)
            counts[case_type] = len(cases)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the gold evaluation queue (gold_case).")
    parser.add_argument("--type", dest="only_type", choices=STRATUM_ORDER, help="build one stratum")
    args = parser.parse_args()

    conn = get_conn()
    try:
        counts = build(conn, only_type=args.only_type)
        conn.commit()
    finally:
        conn.close()

    total = sum(counts.values())
    print("gold_case stratum counts:")
    for case_type in STRATUM_ORDER:
        if case_type in counts:
            print(f"  {case_type:<24} {counts[case_type]:>4}")
    print(f"  {'TOTAL':<24} {total:>4}")


if __name__ == "__main__":
    main()
