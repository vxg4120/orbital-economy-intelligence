"""Shared DB-test helpers for tests/test_ingest_*.py: baseline + cleanup of synthetic rows.

Reserved synthetic NORAD id range for db tests (per task-3 ground rules): 900000001-909999999.
The dev DB is shared with other agents' tests running concurrently, so every db-marked test
must clean up exactly what it created and nothing else.
"""

NORAD_RESERVED_LOW = 900000001
NORAD_RESERVED_HIGH = 909999999

# Raw landing tables that carry ingest_run_id (FK to ingest_run) — must be cleared before the
# ingest_run rows they reference, or the FK blocks the delete.
_RAW_TABLES_BY_RUN = (
    "raw_satcat",
    "raw_gcat_satcat",
    "raw_gcat_psatcat",
    "raw_ucs",
    "raw_supgp_status",
)


def run_id_baseline(conn) -> int:
    """Snapshot the max ingest_run_id before a test runs, so cleanup can target exactly the
    rows the test created."""
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(max(ingest_run_id), 0) FROM ingest_run")
        return cur.fetchone()[0]


def cleanup_since(conn, baseline: int) -> None:
    """Delete every row a test could have created since `baseline`: raw_* tables by
    ingest_run_id, gp_elements by the reserved NORAD range (it has no ingest_run_id column),
    then the ingest_run rows themselves last (FK ordering)."""
    with conn.cursor() as cur:
        for table in _RAW_TABLES_BY_RUN:
            cur.execute(f"DELETE FROM {table} WHERE ingest_run_id > %s", (baseline,))
        cur.execute(
            "DELETE FROM gp_elements WHERE norad_id BETWEEN %s AND %s",
            (NORAD_RESERVED_LOW, NORAD_RESERVED_HIGH),
        )
        cur.execute("DELETE FROM ingest_run WHERE ingest_run_id > %s", (baseline,))
    conn.commit()
