"""Record each feed's claims once, with a validity range (docs/specs/assertion-model.md).

A claim is (source, source_key, attribute, value). `record` takes one run's full set of a
feed's claims and reconciles it with the open claims: anything the run no longer makes is
closed at that run, anything new is opened at it, and anything unchanged is left alone, so a
nightly that changes nothing writes nothing. Re-recording the same run is a no-op. The feed's
rows come from the raw landing tables through the same (attribute, column) lists as
identity/assertions.py, so the two agree row for row.

No commit: the caller owns the transaction.
"""

from __future__ import annotations

from identity.assertions import _GCAT_ATTRS, _SATCAT_ATTRS, _UCS_ATTRS, _latest_run

# The feeds: (raw table, source, key expression, attribute lists).
FEEDS = [
    ("raw_satcat", "satcat", "norad_cat_id", _SATCAT_ATTRS),
    ("raw_gcat_satcat", "gcat", "jcat", _GCAT_ATTRS),
    ("raw_ucs", "ucs", "row_key", _UCS_ATTRS),
]


def _last_recorded_run(cur, source) -> int | None:
    cur.execute(
        "SELECT greatest(max(first_run), max(closed_run)) FROM claim WHERE source = %s", (source,)
    )
    return cur.fetchone()[0]


def record(conn, source: str, run: int, observed_at, rows) -> tuple[int, int]:
    """Reconcile the open claims of `source` with `rows`, the (key, attribute, value) triples
    its run `run` asserts, observed at `observed_at`. Returns (closed, opened).

    A run older than the last one recorded is refused (returns (0, 0)): recording runs out of
    order would close every claim the newer run makes, since the older one lacks them."""
    with conn.cursor() as cur:
        last = _last_recorded_run(cur, source)
        if last is not None and run < last:
            return 0, 0
        cur.execute(
            "CREATE TEMP TABLE _claims_run (source_key text, attribute text, value text) "
            "ON COMMIT DROP"
        )
        with cur.copy("COPY _claims_run (source_key, attribute, value) FROM STDIN") as cp:
            for key, attribute, value in rows:
                cp.write_row((key, attribute, value))
        cur.execute(
            """
            UPDATE claim c SET closed_run = %(run)s, observed_to = %(at)s
            WHERE c.source = %(source)s AND c.closed_run IS NULL
              AND NOT EXISTS (SELECT 1 FROM _claims_run r
                              WHERE r.source_key = c.source_key AND r.attribute = c.attribute
                                AND r.value = c.value)
            """,
            {"run": run, "at": observed_at, "source": source},
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
    """The nightly: every feed's newest snapshot."""
    return {source: record_feed(conn, table, source, key_expr, attrs)
            for table, source, key_expr, attrs in FEEDS}
