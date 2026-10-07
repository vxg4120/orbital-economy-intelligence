"""Record each feed's claims once, with a validity range (docs/specs/assertion-model.md).

A claim is (source, source_key, attribute, value). `record` takes one run's full set of a
feed's claims and reconciles it with the open claims: anything the run no longer makes is
closed at that run, anything new is opened at it, and anything unchanged is left alone, so a
nightly that changes nothing writes nothing but the feed's progress row. The feed's rows come
from the raw landing tables through the same (attribute, column) lists as
identity/assertions.py, so the two agree row for row.

Runs are recorded strictly in order, once each, one writer per feed at a time: claim_progress
holds the last run recorded (changes or not) and a transaction-scoped advisory lock on the feed
serializes the nightly and the history replay. A feed with no progress row is not recorded by
the nightly: scripts/build_claims.py creates it as it replays the history, so history is never
skipped.

No commit: the caller owns the transaction.
"""

from __future__ import annotations

from identity.assertions import _GCAT_ATTRS, _SATCAT_ATTRS, _UCS_ATTRS, _latest_run

# The feeds: (raw table, source, key expression, attribute lists). Only these are snapshot
# feeds; a correction channel (operator_confirmed) is additive and is not recorded here.
FEEDS = [
    ("raw_satcat", "satcat", "norad_cat_id", _SATCAT_ATTRS),
    ("raw_gcat_satcat", "gcat", "jcat", _GCAT_ATTRS),
    ("raw_ucs", "ucs", "row_key", _UCS_ATTRS),
]
SOURCES = tuple(source for _, source, _, _ in FEEDS)


def lock(cur, source: str) -> None:
    """One writer per feed per transaction (nightly or replay); the other waits."""
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('claim:' || %s))", (source,))


def progress(cur, source: str):
    """(last_run, observed_at) for the feed, or None before its history is replayed."""
    cur.execute("SELECT last_run, observed_at FROM claim_progress WHERE source = %s", (source,))
    return cur.fetchone()


def record(conn, source: str, run: int, observed_at, rows, *, bootstrap: bool = False):
    """Reconcile the open claims of `source` with `rows`, the (key, attribute, value) triples
    its run `run` asserts, observed at `observed_at`. Returns (closed, opened).

    Refused, returning (0, 0): a run at or before the feed's last recorded one (recording out
    of order would close everything a newer run makes; recording twice must change nothing); an
    empty run (a snapshot asserting nothing is not evidence that every claim ended); and a feed
    with no progress row unless `bootstrap`, which only the history replay passes."""
    rows = list(rows)
    if not rows:
        return 0, 0
    with conn.cursor() as cur:
        lock(cur, source)
        before = progress(cur, source)
        if before is None and not bootstrap:
            return 0, 0
        if before is not None and run <= before[0]:
            return 0, 0
        cur.execute(
            "CREATE TEMP TABLE _claims_run (source_key text, attribute text, value text) "
            "ON COMMIT DROP"
        )
        with cur.copy("COPY _claims_run (source_key, attribute, value) FROM STDIN") as cp:
            for key, attribute, value in rows:
                cp.write_row((key, attribute, value))
        # Close what the run no longer makes: closed at this run, last observed at the previous.
        cur.execute(
            """
            UPDATE claim c SET closed_run = %(run)s, observed_to = %(seen)s
            WHERE c.source = %(source)s AND c.closed_run IS NULL
              AND NOT EXISTS (SELECT 1 FROM _claims_run r
                              WHERE r.source_key = c.source_key AND r.attribute = c.attribute
                                AND r.value = c.value)
            """,
            {"run": run, "seen": before[1] if before else observed_at, "source": source},
        )
        closed = cur.rowcount
        cur.execute(
            """
            INSERT INTO claim (source, source_key, attribute, value, first_run, observed_from)
            SELECT DISTINCT %(source)s, r.source_key, r.attribute, r.value, %(run)s, %(at)s
            FROM _claims_run r
            WHERE NOT EXISTS (SELECT 1 FROM claim c
                              WHERE c.source = %(source)s AND c.source_key = r.source_key
                                AND c.attribute = r.attribute AND c.closed_run IS NULL)
            """,
            {"run": run, "at": observed_at, "source": source},
        )
        opened = cur.rowcount
        cur.execute("DROP TABLE _claims_run")
        cur.execute(
            "INSERT INTO claim_progress (source, last_run, observed_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (source) DO UPDATE SET last_run = EXCLUDED.last_run, "
            "observed_at = EXCLUDED.observed_at",
            (source, run, observed_at),
        )
    return closed, opened


def _raw_rows(cur, table, key_expr, attrs, run):
    """The (key, attribute, value) triples a raw snapshot asserts, one query per attribute."""
    for attribute, col in attrs:
        cur.execute(
            f"SELECT ({key_expr})::text, %(attr)s, ({col})::text FROM {table} "
            f"WHERE ingest_run_id = %(run)s AND ({col}) IS NOT NULL",
            {"attr": attribute, "run": run},
        )
        yield from cur.fetchall()


def record_feed(conn, table, source, key_expr, attrs) -> tuple[int, int]:
    """Record the feed's newest OK snapshot. Returns (closed, opened), (0, 0) with no snapshot."""
    run = _latest_run(conn, table)
    if run is None:
        return 0, 0
    with conn.cursor() as cur:
        cur.execute(f"SELECT max(loaded_at) FROM {table} WHERE ingest_run_id = %s", (run,))
        observed_at = cur.fetchone()[0]
        rows = list(_raw_rows(cur, table, key_expr, attrs, run))
    return record(conn, source, run, observed_at, rows)


def record_all(conn) -> dict[str, tuple[int, int]]:
    """The nightly: every feed's newest snapshot (feeds not yet bootstrapped record nothing)."""
    return {source: record_feed(conn, table, source, key_expr, attrs)
            for table, source, key_expr, attrs in FEEDS}
