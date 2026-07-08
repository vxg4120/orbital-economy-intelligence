"""Extract per-attribute source claims into source_assertion (provenance before resolution).

Each source's latest OK snapshot yields one assertion per (attribute, row). satellite_id is
filled through the crosswalk (NULL when the row is still unmatched, kept attachable via
source_key). Idempotent per (source, source_key, attribute, ingest_run_id): a re-run inserts
nothing new. No commit — the caller owns the transaction.
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
]
# UCS carries no status column; every listed satellite is "operational as of the freeze".
_UCS_ATTRS = [
    ("owner", "operator"),
    ("name", "name"),
    ("status", "'operational'"),
]


def _latest_run(conn, table: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT max(r.ingest_run_id) FROM {table} r "
            "JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'"
        )
        return cur.fetchone()[0]


def _extract(conn, table, source, key_expr, id_type, attrs, run) -> None:
    """Insert one assertion per (attribute, row) for a source's latest snapshot, idempotently.

    key_expr: SQL yielding the source-native key (also the crosswalk id_value).
    id_type:  crosswalk id_type joining rows back to satellite_id.
    """
    with conn.cursor() as cur:
        for attribute, col in attrs:
            cur.execute(
                f"""
                INSERT INTO source_assertion
                    (satellite_id, source_key, attribute, value, source, observed_at, ingest_run_id)
                SELECT si.satellite_id, ({key_expr})::text, %(attr)s, ({col})::text,
                       %(src)s, r.loaded_at, r.ingest_run_id
                FROM {table} r
                LEFT JOIN satellite_identifier si
                       ON si.id_type = %(id_type)s AND si.source = %(src)s
                      AND si.id_value = ({key_expr})::text
                WHERE r.ingest_run_id = %(run)s
                  AND ({col}) IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM source_assertion a
                      WHERE a.source = %(src)s AND a.source_key = ({key_expr})::text
                        AND a.attribute = %(attr)s AND a.ingest_run_id = r.ingest_run_id
                  )
                """,
                {"attr": attribute, "src": source, "id_type": id_type, "run": run},
            )


def extract(conn) -> None:
    """Extract assertions from SATCAT, GCAT and UCS latest snapshots."""
    srun = _latest_run(conn, "raw_satcat")
    if srun is not None:
        _extract(conn, "raw_satcat", "satcat", "norad_cat_id", "norad", _SATCAT_ATTRS, srun)
    grun = _latest_run(conn, "raw_gcat_satcat")
    if grun is not None:
        _extract(conn, "raw_gcat_satcat", "gcat", "jcat", "gcat_id", _GCAT_ATTRS, grun)
    urun = _latest_run(conn, "raw_ucs")
    if urun is not None:
        _extract(conn, "raw_ucs", "ucs", "row_key", "ucs_row", _UCS_ATTRS, urun)
