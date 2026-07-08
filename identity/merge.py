"""Link and merge operations — the only writers of satellite_identifier + merge_log.

Every link and every merge writes merge_log: there are no silent writes anywhere in
identity/. These functions never commit; the caller (scripts/build_graph.py) owns the
transaction boundary, which keeps links/merges atomic with the surrounding pipeline and
makes DB tests trivially reversible.
"""

from __future__ import annotations

from psycopg.types.json import Jsonb


def link(conn, satellite_id, raw_ref, rule, score, details=None) -> bool:
    """Attach one identifier (raw_ref) to a satellite and log the link.

    raw_ref keys: id_type, id_value, source (required); valid_from, valid_to,
    confidence (optional). Idempotent: the identifier insert is ON CONFLICT DO
    NOTHING against the crosswalk's UNIQUE constraint, and merge_log is written
    only when a new identifier row was actually created (rowcount > 0), so
    re-running the matcher does not spam the audit log. Returns True if a new
    identifier was linked.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO satellite_identifier
                (satellite_id, id_type, id_value, valid_from, valid_to, source, confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_type, id_value, source, satellite_id) DO NOTHING
            """,
            (
                satellite_id,
                raw_ref["id_type"],
                raw_ref["id_value"],
                raw_ref.get("valid_from"),
                raw_ref.get("valid_to"),
                raw_ref["source"],
                raw_ref.get("confidence", 1.00),
            ),
        )
        if not cur.rowcount:
            return False
        payload = dict(details or {})
        payload.setdefault("id_type", raw_ref["id_type"])
        payload.setdefault("id_value", raw_ref["id_value"])
        payload.setdefault("source", raw_ref["source"])
        cur.execute(
            """
            INSERT INTO merge_log (surviving_id, merged_id, rule_fired, score, details)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (satellite_id, satellite_id, rule, score, Jsonb(payload)),
        )
    return True


def merge(conn, surviving_id, merged_id, rule, score, details=None) -> None:
    """Fold merged_id into surviving_id: repoint every child row, log, drop the shell.

    Repoints satellite_identifier, source_assertion, satellite_status_history and
    satellite_operator, deleting any merged-side row that would collide with an
    existing surviving-side row on its natural key first (so no FK/PK violation and
    no orphans are left behind). Writes merge_log, then deletes the merged shell
    satellite. Does not commit.
    """
    if surviving_id == merged_id:
        raise ValueError("cannot merge a satellite into itself")
    with conn.cursor() as cur:
        # satellite_identifier: UNIQUE (id_type, id_value, source, satellite_id)
        cur.execute(
            """
            DELETE FROM satellite_identifier m
            WHERE m.satellite_id = %(merged)s
              AND EXISTS (
                  SELECT 1 FROM satellite_identifier s
                  WHERE s.satellite_id = %(surv)s
                    AND s.id_type = m.id_type AND s.id_value = m.id_value AND s.source = m.source
              )
            """,
            {"merged": merged_id, "surv": surviving_id},
        )
        cur.execute(
            "UPDATE satellite_identifier SET satellite_id = %s WHERE satellite_id = %s",
            (surviving_id, merged_id),
        )
        # source_assertion: no per-satellite natural key -> straight repoint
        cur.execute(
            "UPDATE source_assertion SET satellite_id = %s WHERE satellite_id = %s",
            (surviving_id, merged_id),
        )
        # satellite_status_history: PK (satellite_id, observed_at, source)
        cur.execute(
            """
            DELETE FROM satellite_status_history m
            WHERE m.satellite_id = %(merged)s
              AND EXISTS (
                  SELECT 1 FROM satellite_status_history s
                  WHERE s.satellite_id = %(surv)s
                    AND s.observed_at = m.observed_at AND s.source = m.source
              )
            """,
            {"merged": merged_id, "surv": surviving_id},
        )
        cur.execute(
            "UPDATE satellite_status_history SET satellite_id = %s WHERE satellite_id = %s",
            (surviving_id, merged_id),
        )
        # satellite_operator: PK (satellite_id, operator_id, role, valid_from)
        cur.execute(
            """
            DELETE FROM satellite_operator m
            WHERE m.satellite_id = %(merged)s
              AND EXISTS (
                  SELECT 1 FROM satellite_operator s
                  WHERE s.satellite_id = %(surv)s
                    AND s.operator_id = m.operator_id AND s.role = m.role
                    AND s.valid_from = m.valid_from
              )
            """,
            {"merged": merged_id, "surv": surviving_id},
        )
        cur.execute(
            "UPDATE satellite_operator SET satellite_id = %s WHERE satellite_id = %s",
            (surviving_id, merged_id),
        )
        cur.execute(
            """
            INSERT INTO merge_log (surviving_id, merged_id, rule_fired, score, details)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (surviving_id, merged_id, rule, score, Jsonb(dict(details or {}))),
        )
        cur.execute("DELETE FROM satellite WHERE satellite_id = %s", (merged_id,))
