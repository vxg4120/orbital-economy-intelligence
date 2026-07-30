"""Catalog key churn: observe it, measure it, and retire what it contaminated.

Fresh multi-payload launches go through a window where the catalogs are still deciding which
object occupies which provisional key. GCAT has been observed doing this two different ways in
one month: renumbering catalog ids against COSPAR pieces (67 of 73 on one launch), and later
re-identifying which satellite sits in each unchanged slot (41 of 73). Both are invisible to
key-based joins, which is how the identity graph once served three satellites under the wrong
names and dropped a fourth entirely.

Three set-based passes, run nightly inside build_graph's transaction:

  detect()             one row in catalog_key_churn per observed referent change between the two
                       most recent OK GCAT snapshots, per key type. Absence is never the signal
                       (catalog ids do not vanish between snapshots); a changed normalized name
                       under the same key is.
  measure_stability()  the same comparison aggregated into key_stability, per (source, id_type),
                       every number carrying its denominator. This is what makes "norad is
                       stable, jcat is not" a measurement instead of an assumption.
  expire_contested()   satellite_identifier rows retire (valid_to set) only when all three hold:
                       the key type is volatile, the key has been OBSERVED to churn, and the
                       satellite it points at has no permanent anchor. Anchored satellites are
                       never touched: legitimate renames land on anchored objects and must not
                       retract anything.

Every expiry writes identity_event('identifier_expired'). No commit here; the caller owns the
transaction (same contract as the rest of identity/).
"""

from __future__ import annotations

from psycopg.types.json import Jsonb

# The two most recent OK GCAT snapshot runs, plus each run's payload projection with the
# normalized name key. Shared prefix for detection and measurement so they cannot disagree.
_RUNS_CTE = """
runs AS (
    SELECT r.ingest_run_id FROM raw_gcat_satcat r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
    WHERE i.status = 'ok' GROUP BY 1 ORDER BY 1 DESC LIMIT 2
),
b AS (SELECT max(ingest_run_id) AS curr, min(ingest_run_id) AS prev FROM runs),
a AS (
    SELECT jcat, piece, COALESCE(pl_name, name) AS nm,
           oei_name_key(COALESCE(pl_name, name)) AS nk, owner,
           (norad_id IS NOT NULL) AS anchored
    FROM raw_gcat_satcat, b
    WHERE ingest_run_id = b.prev AND object_type LIKE 'P%'
),
z AS (
    SELECT jcat, piece, COALESCE(pl_name, name) AS nm,
           oei_name_key(COALESCE(pl_name, name)) AS nk, owner,
           (norad_id IS NOT NULL) AS anchored
    FROM raw_gcat_satcat, b
    WHERE ingest_run_id = b.curr AND object_type LIKE 'P%'
)
"""

_DETECT_SQL = "WITH " + _RUNS_CTE + """
INSERT INTO catalog_key_churn (source, id_type, id_value, prev_run_id, curr_run_id,
                               prev_name, curr_name, prev_name_key, curr_name_key,
                               prev_owner, curr_owner, prev_anchored, launch_key)
SELECT 'gcat', 'gcat_id', a.jcat, b.prev, b.curr, a.nm, z.nm, a.nk, z.nk,
       a.owner, z.owner, a.anchored, oei_launch_key(a.piece)
FROM a JOIN z USING (jcat), b WHERE a.nk IS DISTINCT FROM z.nk
UNION ALL
SELECT 'gcat', 'cospar', a.piece, b.prev, b.curr, a.nm, z.nm, a.nk, z.nk,
       a.owner, z.owner, a.anchored, oei_launch_key(a.piece)
FROM a JOIN z USING (piece), b WHERE a.nk IS DISTINCT FROM z.nk
ON CONFLICT DO NOTHING
"""

_STABILITY_SQL = "WITH " + _RUNS_CTE + """
INSERT INTO key_stability (source, id_type, observations, referent_changes, changes_anchored,
                           prev_run_id, curr_run_id)
SELECT 'gcat', 'gcat_id',
       count(*),
       count(*) FILTER (WHERE a.nk IS DISTINCT FROM z.nk),
       count(*) FILTER (WHERE a.nk IS DISTINCT FROM z.nk AND a.anchored),
       b.prev, b.curr
FROM a JOIN z USING (jcat), b GROUP BY b.prev, b.curr
UNION ALL
SELECT 'gcat', 'cospar',
       count(*),
       count(*) FILTER (WHERE a.nk IS DISTINCT FROM z.nk),
       count(*) FILTER (WHERE a.nk IS DISTINCT FROM z.nk AND a.anchored),
       b.prev, b.curr
FROM a JOIN z USING (piece), b GROUP BY b.prev, b.curr
ON CONFLICT DO NOTHING
"""

_EXPIRE_SELECT_SQL = """
SELECT si.identifier_id, si.satellite_id, si.id_type, si.id_value, si.source
FROM satellite_identifier si
WHERE si.valid_to IS NULL
  AND si.id_type IN ('gcat_id', 'cospar')
  AND EXISTS (SELECT 1 FROM catalog_key_churn c
              WHERE c.id_type = si.id_type AND c.id_value = si.id_value)
  AND EXISTS (SELECT 1 FROM satellite s
              WHERE s.satellite_id = si.satellite_id AND s.anchor_state = 'provisional')
"""


def detect(conn) -> int:
    """Record every observed referent change between the last two OK GCAT snapshots."""
    with conn.cursor() as cur:
        cur.execute(_DETECT_SQL)
        return cur.rowcount


def measure_stability(conn) -> int:
    """Aggregate the same comparison into per-key-type stability rows with denominators."""
    with conn.cursor() as cur:
        cur.execute(_STABILITY_SQL)
        return cur.rowcount


def refresh_anchor_state(conn) -> int:
    """Idempotent backstop: any satellite carrying a NORAD id is anchored. The matchers set
    this on creation; this catches satellites that gained their anchor through a later path."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE satellite SET anchor_state = 'anchored', anchor_source = 'satcat' "
            "WHERE norad_id IS NOT NULL AND anchor_state <> 'anchored'"
        )
        return cur.rowcount


def expire_contested(conn) -> int:
    """Retire volatile identifiers that observed churn AND point at unanchored satellites.

    The three-way conjunction is the safety: anchored satellites are never touched (legitimate
    renames land on anchored objects), untouched keys are never touched (no churn observed means
    no evidence), and stable key types are never touched at all. Each expiry is logged to
    identity_event so no identity write is silent.
    """
    with conn.cursor() as cur:
        cur.execute(_EXPIRE_SELECT_SQL)
        victims = cur.fetchall()
        if not victims:
            return 0
        cur.execute(
            "UPDATE satellite_identifier SET valid_to = "
            "(SELECT max(started_at)::date FROM ingest_run WHERE status = 'ok') "
            "WHERE identifier_id = ANY(%s)",
            ([v[0] for v in victims],),
        )
        expired = cur.rowcount
        for _identifier_id, sat_id, id_type, id_value, source in victims:
            cur.execute(
                "INSERT INTO identity_event (satellite_id, event, rule_fired, details) "
                "VALUES (%s, 'identifier_expired', 'expire_contested_on_provisional', %s)",
                (sat_id, Jsonb({"id_type": id_type, "id_value": id_value, "source": source})),
            )
    return expired


def run_all(conn) -> dict:
    """The nightly sequence: observe, measure, re-anchor, then expire what the observations
    justify. Ordering matters: expiry reads the churn rows detect just wrote, and promotion
    (identity/reconcile.py, which runs before this in build_graph) removes provisional records
    so freshly promoted satellites are anchored before expiry looks at them."""
    return {
        "churn_rows": detect(conn),
        "stability_rows": measure_stability(conn),
        "anchor_refreshed": refresh_anchor_state(conn),
        "identifiers_expired": expire_contested(conn),
    }
