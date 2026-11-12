"""Record each feed's claims once, with a validity range (docs/specs/assertion-model.md).

A claim is (source, source_key, attribute, value). `record` takes one run's full set of a
feed's claims and reconciles it with the open claims: anything the run no longer makes is
closed at that run, anything new is opened at it, and anything unchanged is left alone, so a
nightly that changes nothing writes nothing but the feed's progress row. The feed's rows come
from the raw landing tables through the (attribute, column) lists below; satellite_id is not
stored, the views join each claim to its satellite through the current crosswalk link.

Runs are recorded strictly in order, once each, one writer per feed at a time: claim_progress
holds the last run recorded (changes or not) and a transaction-scoped advisory lock on the feed
serializes concurrent writers. A feed with no progress row starts at its newest run (the
per-run ledger this replaced, and its history replay, retired 2026-10-12).

No commit: the caller owns the transaction.
"""

from __future__ import annotations

# (attribute, raw column expression) per source. name uses the most commercial field available.
_SATCAT_ATTRS = [
    ("owner", "owner"),
    ("status", "ops_status_code"),
    ("decay_date", "decay_date::text"),
    ("object_type", "object_type"),
    ("name", "object_name"),
]
_GCAT_ATTRS = [
    ("owner", "owner"),
    ("status", "status"),
    ("decay_date", "decay_date"),
    ("object_type", "object_type"),
    ("name", "coalesce(pl_name, name)"),
    # Bus Benchmarks provenance: the raw platform / manufacturer claims behind satellite_bus.
    # '-' is GCAT's explicit "no value" and would be noise as a claim, so it maps to NULL (and
    # the NOT NULL filter in _raw_rows drops it).
    ("bus", "nullif(nullif(bus, '-'), '')"),
    ("manufacturer", "nullif(nullif(manufacturer, '-'), '')"),
]
# UCS carries no status column; every listed satellite is "operational as of the freeze".
_UCS_ATTRS = [
    ("owner", "operator"),
    ("name", "name"),
    ("status", "'operational'"),
]

# The feeds: (raw table, source, key expression, attribute lists). Only these are snapshot
# feeds; a correction channel (operator_confirmed) is additive and is not recorded here.
FEEDS = [
    ("raw_satcat", "satcat", "norad_cat_id", _SATCAT_ATTRS),
    ("raw_gcat_satcat", "gcat", "jcat", _GCAT_ATTRS),
    ("raw_ucs", "ucs", "row_key", _UCS_ATTRS),
]

# Space-Track's actual decays (docs/specs/spacetrack-topup.md, migration 0028). Each landing
# holds only the last 30 days of Historical messages, so every OK landing is first folded, oldest
# first, into spacetrack_decay_message (every message ever seen), and the feed is that table's
# best surviving message per object, recorded as an ordinary snapshot keyed by NORAD id.
SPACETRACK_DECAY = "spacetrack_decay"
# A landing's window is "MSG_EPOCH > now-30" on Space-Track's clock. A message missing from a
# landing is withdrawn only when its epoch is inside the window with a day to spare: nearer the
# edge it may simply have aged out between Space-Track's clock and the landing's start.
_WITHDRAW_INSIDE = "29 days"

_FOLD_SEEN_SQL = """
INSERT INTO spacetrack_decay_message AS m
    (norad_cat_id, msg_epoch, decay_epoch, decay_date, precedence, source, first_run, last_run)
SELECT DISTINCT ON (norad_cat_id, msg_epoch, decay_epoch, precedence, source)
       norad_cat_id, msg_epoch, decay_epoch, decay_date, precedence, source,
       %(run)s, %(run)s
FROM raw_spacetrack_decay
WHERE ingest_run_id = %(run)s AND msg_type = 'Historical'
ON CONFLICT ON CONSTRAINT spacetrack_decay_message_uq
DO UPDATE SET last_run = EXCLUDED.last_run, withdrawn_run = NULL
"""
_FOLD_WITHDRAWN_SQL = f"""
UPDATE spacetrack_decay_message m SET withdrawn_run = %(run)s
WHERE m.withdrawn_run IS NULL AND m.last_run < %(run)s
  AND m.msg_epoch > (SELECT started_at FROM ingest_run WHERE ingest_run_id = %(run)s)
                    - interval '{_WITHDRAW_INSIDE}'
"""
_SNAPSHOT_SQL = """
SELECT DISTINCT ON (norad_cat_id) norad_cat_id::text, 'decay_date', decay_date::text
FROM spacetrack_decay_message
WHERE withdrawn_run IS NULL AND decay_date IS NOT NULL
ORDER BY norad_cat_id, precedence ASC NULLS LAST, msg_epoch DESC NULLS LAST, decay_date DESC
"""

SOURCES = tuple(source for _, source, _, _ in FEEDS) + (SPACETRACK_DECAY,)


def lock(cur, source: str) -> None:
    """One writer per feed per transaction (nightly or replay); the other waits."""
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('claim:' || %s))", (source,))


def progress(cur, source: str):
    """(last_run, observed_at) for the feed, or None before its first run is recorded."""
    cur.execute("SELECT last_run, observed_at FROM claim_progress WHERE source = %s", (source,))
    return cur.fetchone()


def record(conn, source: str, run: int, observed_at, rows):
    """Reconcile the open claims of `source` with `rows`, the (key, attribute, value) triples
    its run `run` asserts, observed at `observed_at`. Returns (closed, opened).

    Refused, returning (0, 0): a run at or before the feed's last recorded one (recording out
    of order would close everything a newer run makes; recording twice must change nothing),
    and an empty run (a snapshot asserting nothing is not evidence that every claim ended)."""
    rows = list(rows)
    if not rows:
        return 0, 0
    with conn.cursor() as cur:
        lock(cur, source)
        before = progress(cur, source)
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


def _latest_run(conn, table: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT max(r.ingest_run_id) FROM {table} r "
            "JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'"
        )
        return cur.fetchone()[0]


def record_feed(conn, table, source, key_expr, attrs) -> tuple[int, int]:
    """Record the feed's newest OK snapshot. Returns (closed, opened): (0, 0) with no snapshot
    or nothing new."""
    run = _latest_run(conn, table)
    if run is None:
        return 0, 0
    with conn.cursor() as cur:
        # Checked before the snapshot is fetched: record() refuses an old run anyway, but a
        # run already recorded should not cost the nightly a 100 MB fetch to find that out.
        before = progress(cur, source)
        if before is not None and run <= before[0]:
            return 0, 0
        cur.execute(f"SELECT max(loaded_at) FROM {table} WHERE ingest_run_id = %s", (run,))
        observed_at = cur.fetchone()[0]
        rows = list(_raw_rows(cur, table, key_expr, attrs, run))
    return record(conn, source, run, observed_at, rows)


def _unfolded_runs(cur, after: int | None) -> list[int]:
    """The OK decay landings newer than `after` that carry rows, oldest first. A landing with
    no rows is never folded: it is no evidence that every message in its window was withdrawn."""
    cur.execute(
        "SELECT DISTINCT r.ingest_run_id FROM raw_spacetrack_decay r "
        "JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id "
        "WHERE i.status = 'ok' AND r.ingest_run_id > %s ORDER BY 1",
        (after if after is not None else 0,),
    )
    return [row[0] for row in cur.fetchall()]


def record_spacetrack_decay(conn) -> tuple[int, int]:
    """Fold every unfolded OK decay landing into spacetrack_decay_message, oldest first (on the
    first night, every retained one), then record the best surviving message per object as the
    `spacetrack_decay` snapshot at the newest landing. Returns (closed, opened): (0, 0) with
    nothing new."""
    with conn.cursor() as cur:
        lock(cur, SPACETRACK_DECAY)
        before = progress(cur, SPACETRACK_DECAY)
        runs = _unfolded_runs(cur, before[0] if before else None)
        if not runs:
            return 0, 0
        for run in runs:
            cur.execute(_FOLD_SEEN_SQL, {"run": run})
            cur.execute(_FOLD_WITHDRAWN_SQL, {"run": run})
        newest = runs[-1]
        cur.execute("SELECT max(loaded_at) FROM raw_spacetrack_decay WHERE ingest_run_id = %s",
                    (newest,))
        observed_at = cur.fetchone()[0]
        cur.execute(_SNAPSHOT_SQL)
        rows = cur.fetchall()
    if not rows:
        return 0, 0
    return record(conn, SPACETRACK_DECAY, newest, observed_at, rows)


def record_all(conn) -> dict[str, tuple[int, int]]:
    """The nightly: every snapshot feed's newest run, then the folded Space-Track decays."""
    stats = {source: record_feed(conn, table, source, key_expr, attrs)
             for table, source, key_expr, attrs in FEEDS}
    stats[SPACETRACK_DECAY] = record_spacetrack_decay(conn)
    return stats
