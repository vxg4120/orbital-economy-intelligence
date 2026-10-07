"""Compare the claim table with the per-run ledger it replaces (docs/specs/assertion-model.md).

The acceptance check between the history replay (scripts/build_claims.py) and the readers' move
to v_current_claim: the current claims per satellite must be the same rows the readers get today
from source_assertion (each feed's newest run, through current crosswalk links), and each feed's
progress must sit at its newest run. Read-only; prints counts and up to 20 examples per side.

The resolver read differently: the newest copy over every retained run, so a value a feed
stopped making lived on until the run that carried it was pruned. On the claim model such a
value is closed and gone (docs/specs/assertion-model.md names the difference); the third
section counts, per attribute, the (satellite, source) winners the resolver would lose or
change, so the difference is measured before the readers move.

Exit status 0 when both EXCEPTs are empty and progress is current, 1 otherwise; the resolver
section is reported, not judged.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.db import get_conn  # noqa: E402
from identity import claims  # noqa: E402

# The ledger's current rows, scanned once into a temp table: each feed's newest run (the
# readers' CURRENT_ASSERTIONS), then the current-link check on those rows only. Applied to the
# scan instead, the planner calls claim_is_current on every one of 37M rows (15 minutes and
# counting on 2026-10-07, against 2 minutes this way), and the comparison needs it three times.
LEDGER = """
    CREATE TEMP TABLE _ledger AS
    WITH l AS MATERIALIZED (
        SELECT source, max(ingest_run_id) AS run FROM source_assertion
        WHERE source = ANY(%(sources)s) GROUP BY source
    ),
    cur AS MATERIALIZED (
        SELECT a.satellite_id, a.source, a.source_key, a.attribute, a.value
        FROM source_assertion a JOIN l ON l.source = a.source AND l.run = a.ingest_run_id
        WHERE a.satellite_id IS NOT NULL
    )
    SELECT * FROM cur WHERE claim_is_current(satellite_id, source, source_key)
"""
OLD = "SELECT satellite_id, source, source_key, attribute, value FROM _ledger"
NEW = """
    SELECT satellite_id, source, source_key, attribute, value
    FROM v_current_claim WHERE source = ANY(%(sources)s)
"""
# identity/resolve.py::_assertions as it read the ledger (newest observed_at per (satellite,
# source) over every retained run, current keys only) against the claim model's winner. This
# one has to scan the attribute's rows over every run: that is what the resolver did nightly.
RESOLVER = """
    WITH newest AS (
        SELECT satellite_id, source, max(observed_at) AS observed_at
        FROM source_assertion
        WHERE attribute = %(attribute)s AND satellite_id IS NOT NULL AND source = ANY(%(sources)s)
        GROUP BY 1, 2
    ),
    old AS (
        SELECT satellite_id, source, value FROM (
            SELECT DISTINCT ON (a.satellite_id, a.source) a.satellite_id, a.source, a.source_key, a.value
            FROM source_assertion a JOIN newest n USING (satellite_id, source, observed_at)
            WHERE a.attribute = %(attribute)s
            ORDER BY a.satellite_id, a.source, a.ingest_run_id DESC, a.source_key DESC
        ) w WHERE claim_is_current(satellite_id, source, source_key)
    ),
    new AS (
        SELECT DISTINCT ON (satellite_id, source) satellite_id, source, value
        FROM v_current_claim WHERE attribute = %(attribute)s AND source = ANY(%(sources)s)
        ORDER BY satellite_id, source, source_key DESC
    )
    SELECT count(*) FILTER (WHERE new.value IS NULL),
           count(*) FILTER (WHERE new.value IS NOT NULL AND new.value <> old.value),
           count(*) FILTER (WHERE new.value = old.value)
    FROM old LEFT JOIN new USING (satellite_id, source)
"""


def main() -> int:
    conn = get_conn()
    ok = True
    params = {"sources": list(claims.SOURCES)}
    with conn.cursor() as cur:
        cur.execute("SET temp_file_limit = '2GB'")
        cur.execute("SET statement_timeout = '30min'")
        started = time.time()
        cur.execute(LEDGER, params)
        print(f"ledger scanned in {time.time() - started:.0f}s", flush=True)
        for label, sql in (("ledger (newest run, current links)", OLD), ("v_current_claim", NEW)):
            cur.execute(f"SELECT count(*) FROM ({sql}) x", params)
            print(f"{label}: {cur.fetchone()[0]:,} rows", flush=True)
        for label, left, right in (("ledger - claims", OLD, NEW), ("claims - ledger", NEW, OLD)):
            cur.execute(f"SELECT count(*) FROM (({left}) EXCEPT ({right})) x", params)
            n = cur.fetchone()[0]
            print(f"{label}: {n:,}", flush=True)
            if n:
                ok = False
                cur.execute(f"({left}) EXCEPT ({right}) ORDER BY 2, 4, 1 LIMIT 20", params)
                for row in cur.fetchall():
                    print("   ", row)
        for attribute in ("name", "object_type", "owner", "status", "decay_date"):
            cur.execute(RESOLVER, {"attribute": attribute, **params})
            lost, changed, same = cur.fetchone()
            print(f"resolver {attribute}: {same:,} same, {changed:,} changed, "
                  f"{lost:,} no longer claimed", flush=True)
        cur.execute(
            "SELECT p.source, p.last_run, l.run FROM claim_progress p "
            "LEFT JOIN (SELECT source, max(ingest_run_id) AS run FROM source_assertion "
            "           GROUP BY source) l USING (source) ORDER BY 1"
        )
        rows = cur.fetchall()
        for source, last, newest in rows:
            flag = "" if last == newest else "   <- behind"
            print(f"progress {source}: last_run {last}, newest ledger run {newest}{flag}")
            ok = ok and last == newest
        # A feed with ledger rows but no progress row was never replayed; a feed with neither
        # (UCS on production, 2026-10-07) has no history to replay and is noted, not failed.
        cur.execute("SELECT DISTINCT source FROM _ledger")
        in_ledger = {r[0] for r in cur.fetchall()}
        recorded = {r[0] for r in rows}
        for source in sorted(set(claims.SOURCES) - recorded):
            print(f"{source}: " + ("in the ledger but not replayed" if source in in_ledger
                                   else "no history on this database"))
            ok = ok and source not in in_ledger
        cur.execute("SELECT count(*), count(*) FILTER (WHERE closed_run IS NULL), "
                    "pg_size_pretty(pg_total_relation_size('claim')) FROM claim")
        total, open_, size = cur.fetchone()
        print(f"claim: {total:,} rows, {open_:,} open, {size}")
    conn.close()
    print("OK" if ok else "DIFFERENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
