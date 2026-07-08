"""Conflict endpoints -- the visible payoff of cross-source resolution.

Three conflict classes, each mirroring a section of quality/report.py exactly so the terminal and
the DQ report never disagree:
  * status      -- SATCAT vs GCAT canonical-status disagreement (report.py section 1)
  * decay       -- decay-*date* disagreement across sources, compared as parsed dates so
                   "1957 Dec  1 1000?" and "1957-12-01" are NOT a conflict (report.py section 2)
  * stale-owners-- latest SATCAT owner resolves to a since-acquired company (report.py section 3)

The status/stale queries carry the deterministic tiebreakers (observed_at DESC, ingest_run_id
DESC, source_key) verbatim. The count_* helpers back the /api/stats conflict tallies.
"""

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from identity.normalize import parse_date_loose

router = APIRouter(prefix="/conflicts", tags=["conflicts"])


# --- status disagreements (report.py _section_status_disagreements + satellite_id) -----------
_STATUS_SQL = """
WITH satcat AS (
    SELECT DISTINCT ON (a.satellite_id) a.satellite_id, m.canonical_status
    FROM source_assertion a
    JOIN status_mapping m ON m.source = 'satcat' AND m.source_value = a.value
    WHERE a.source = 'satcat' AND a.attribute = 'status' AND a.satellite_id IS NOT NULL
    ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
),
gcat AS (
    SELECT DISTINCT ON (a.satellite_id) a.satellite_id, m.canonical_status
    FROM source_assertion a
    JOIN status_mapping m ON m.source = 'gcat' AND m.source_value = a.value
    WHERE a.source = 'gcat' AND a.attribute = 'status' AND a.satellite_id IS NOT NULL
    ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
),
disagree AS (
    SELECT
        s.satellite_id,
        s.norad_id,
        s.canonical_name,
        sc.canonical_status AS satcat_status,
        gc.canonical_status AS gcat_status
    FROM satcat sc
    JOIN gcat gc ON gc.satellite_id = sc.satellite_id
    JOIN satellite s ON s.satellite_id = sc.satellite_id
    WHERE sc.canonical_status <> gc.canonical_status
      AND sc.canonical_status <> 'UNKNOWN'
      AND gc.canonical_status <> 'UNKNOWN'
    ORDER BY s.norad_id NULLS LAST, s.satellite_id
)
"""

# --- stale post-M&A owners (report.py _section_stale_post_ma_owners + satellite_id) ----------
_STALE_SQL = """
WITH latest_satcat_owner AS (
    SELECT DISTINCT ON (satellite_id) satellite_id, value AS owner_raw
    FROM source_assertion
    WHERE attribute = 'owner' AND source = 'satcat' AND satellite_id IS NOT NULL
    ORDER BY satellite_id, observed_at DESC, ingest_run_id DESC, source_key
),
owner_operator AS (
    SELECT lso.satellite_id, lso.owner_raw, oa.operator_id
    FROM latest_satcat_owner lso
    JOIN operator_alias oa
        ON oa.source = 'satcat' AND lower(oa.alias) = lower(lso.owner_raw)
),
stale AS (
    SELECT
        s.satellite_id,
        s.norad_id,
        s.canonical_name,
        oo.owner_raw AS catalog_owner,
        o_child.canonical_name AS resolved_operator,
        o_parent.canonical_name AS acquired_by,
        orl.valid_from AS acquisition_date
    FROM owner_operator oo
    JOIN satellite s ON s.satellite_id = oo.satellite_id
    JOIN operator o_child ON o_child.operator_id = oo.operator_id
    JOIN operator_relationship orl
        ON orl.child_id = oo.operator_id
       AND orl.relationship IN ('acquired_by', 'merged_into')
       AND orl.valid_from <= current_date
       AND (orl.valid_to IS NULL OR orl.valid_to > current_date)
    JOIN operator o_parent ON o_parent.operator_id = orl.parent_id
    ORDER BY s.norad_id NULLS LAST, s.satellite_id
)
"""

# --- decay-date claims (report.py _section_decay_date_conflicts, resolved to dates in Python) --
_DECAY_CLAIMS_SQL = """
SELECT s.satellite_id, s.norad_id, s.canonical_name, l.source, l.value
FROM (
    SELECT DISTINCT ON (satellite_id, source) satellite_id, source, value, observed_at
    FROM source_assertion
    WHERE attribute = 'decay_date' AND satellite_id IS NOT NULL
    ORDER BY satellite_id, source, observed_at DESC, ingest_run_id DESC, source_key
) l
JOIN satellite s ON s.satellite_id = l.satellite_id
ORDER BY s.norad_id NULLS LAST, l.satellite_id, l.source
"""


def _decay_conflict_rows(db) -> list[dict]:
    """All satellites with a genuine cross-source decay-*date* disagreement, report.py order.

    Groups each satellite's per-source claims (already ordered), parses each raw value to a date,
    and keeps only satellites with more than one distinct parseable date -- so pure formatting
    differences never read as a conflict. Raw claims are preserved in sources_and_dates.
    """
    with db.cursor() as cur:
        cur.execute(_DECAY_CLAIMS_SQL)
        claims = cur.fetchall()

    per_sat: dict = {}
    for row in claims:
        entry = per_sat.setdefault(
            row["satellite_id"],
            {"satellite_id": row["satellite_id"], "norad_id": row["norad_id"],
             "canonical_name": row["canonical_name"], "claims": []},
        )
        entry["claims"].append((row["source"], row["value"]))

    rows = []
    for entry in per_sat.values():
        parsed = {parse_date_loose(v) for _, v in entry["claims"]}
        parsed.discard(None)  # an unparseable value can't establish a date conflict
        if len(parsed) > 1:
            rows.append({
                "satellite_id": entry["satellite_id"],
                "norad_id": entry["norad_id"],
                "canonical_name": entry["canonical_name"],
                "sources_and_dates": "; ".join(f"{s}: {v}" for s, v in entry["claims"]),
            })
    return rows


def count_status_conflicts(db) -> int:
    with db.cursor() as cur:
        cur.execute(_STATUS_SQL + "SELECT count(*) AS n FROM disagree")
        return cur.fetchone()["n"]


def count_stale_owners(db) -> int:
    with db.cursor() as cur:
        cur.execute(_STALE_SQL + "SELECT count(*) AS n FROM stale")
        return cur.fetchone()["n"]


def count_decay_conflicts(db) -> int:
    return len(_decay_conflict_rows(db))


def _paginate_sql(db, cte: str, source: str, limit: int, offset: int) -> tuple[list, int]:
    """Run ``cte`` + a windowed SELECT over ``source``, returning (page rows, total)."""
    with db.cursor() as cur:
        cur.execute(
            cte + f"SELECT *, count(*) OVER() AS total FROM {source} LIMIT %s OFFSET %s",
            (limit, offset),
        )
        rows = cur.fetchall()
    total = rows[0]["total"] if rows else 0
    for r in rows:
        r.pop("total", None)
    return rows, total


@router.get("/status")
def conflicts_status(
    db=Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    rows, total = _paginate_sql(db, _STATUS_SQL, "disagree", limit, offset)
    return {"rows": rows, "total": total}


@router.get("/stale-owners")
def conflicts_stale_owners(
    db=Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    rows, total = _paginate_sql(db, _STALE_SQL, "stale", limit, offset)
    return {"rows": rows, "total": total}


@router.get("/decay")
def conflicts_decay(
    db=Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    all_rows = _decay_conflict_rows(db)
    return {"rows": all_rows[offset:offset + limit], "total": len(all_rows)}
