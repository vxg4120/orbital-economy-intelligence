"""Phase 1 identity pipeline CLI.

Seeds operators + status_mapping from the curated YAML (idempotent upserts), runs the matcher
(deterministic then probabilistic), extracts source assertions, resolves winners into the
dimension tables, and prints a summary. Seeding and every phase write are idempotent, so the
whole build is safe to re-run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.db import get_conn  # noqa: E402
from identity import assertions, match, resolve  # noqa: E402

_OPERATOR_SEED = REPO_ROOT / "identity" / "operator_seed.yml"
_STATUS_MAP = REPO_ROOT / "identity" / "status_map.yml"
_REVIEW_CSV = REPO_ROOT / "data" / "review" / "match_review.csv"


# --- seeding ------------------------------------------------------------------


def seed_operators(conn, path=_OPERATOR_SEED) -> None:
    """Upsert operators, aliases (canonical/aliases/satcat_codes/gcat_codes) and relationships."""
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    ids: dict[str, int] = {}
    with conn.cursor() as cur:
        for op in doc.get("operators", []):
            cur.execute(
                """
                INSERT INTO operator (canonical_name, country, operator_class)
                VALUES (%s, %s, %s)
                ON CONFLICT (canonical_name)
                DO UPDATE SET country = EXCLUDED.country,
                             operator_class = EXCLUDED.operator_class
                RETURNING operator_id
                """,
                (op["name"], op.get("country"), op.get("class")),
            )
            oid = cur.fetchone()[0]
            ids[op["name"]] = oid
            aliases = [(a, "seed") for a in [op["name"], *op.get("aliases", [])]]
            aliases += [(c, "satcat") for c in op.get("satcat_codes", [])]
            aliases += [(c, "gcat") for c in op.get("gcat_codes", [])]
            for alias, src in aliases:
                cur.execute(
                    "INSERT INTO operator_alias (operator_id, alias, source) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (oid, alias, src),
                )
        for rel in doc.get("relationships", []):
            child, parent = ids.get(rel["child"]), ids.get(rel["parent"])
            if child is None or parent is None:
                raise ValueError(f"relationship references unknown operator: {rel}")
            cur.execute(
                """
                INSERT INTO operator_relationship
                    (child_id, parent_id, relationship, valid_from, valid_to, source)
                VALUES (%s, %s, %s, %s, NULL, 'seed')
                ON CONFLICT DO NOTHING
                """,
                (child, parent, rel["relationship"], rel["valid_from"]),
            )


def seed_status_map(conn, path=_STATUS_MAP) -> None:
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    with conn.cursor() as cur:
        for source, codes in doc.items():
            for source_value, spec in codes.items():
                cur.execute(
                    """
                    INSERT INTO status_mapping (source, source_value, canonical_status, notes)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (source, source_value)
                    DO UPDATE SET canonical_status = EXCLUDED.canonical_status,
                                 notes = EXCLUDED.notes
                    """,
                    (source, source_value, spec["canonical"], spec.get("notes")),
                )


# --- pipeline -----------------------------------------------------------------


def run_pipeline(conn, review_csv=_REVIEW_CSV) -> dict:
    """Seed + match + assert + resolve, without committing. Returns a summary dict."""
    seed_operators(conn)
    seed_status_map(conn)
    prob_stats = match.run_matchers(conn, review_csv=review_csv)
    assertions.extract(conn)
    resolve_stats = resolve.resolve(conn)
    return summarize(conn, prob_stats, resolve_stats, review_csv)


def summarize(conn, prob_stats, resolve_stats, review_csv) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM satellite")
        satellites = cur.fetchone()[0]
        cur.execute("SELECT id_type, count(*) FROM satellite_identifier GROUP BY id_type")
        identifiers = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM merge_log")
        merge_rows = cur.fetchone()[0]
        cur.execute("SELECT source, count(*) FROM source_assertion GROUP BY source")
        assertions_by_source = dict(cur.fetchall())
    review_size = _review_size(review_csv)
    total = satellites or 1
    return {
        "satellites": satellites,
        "identifiers_by_type": identifiers,
        "merge_log_rows": merge_rows,
        "assertions_by_source": assertions_by_source,
        "operator_resolved": resolve_stats.get("operator_resolved", 0),
        "status_resolved": resolve_stats.get("status_resolved", 0),
        "operator_coverage_pct": round(100 * resolve_stats.get("operator_resolved", 0) / total, 1),
        "status_coverage_pct": round(100 * resolve_stats.get("status_resolved", 0) / total, 1),
        "unmapped_status": resolve_stats.get("unmapped_status", []),
        "unmatched_owners": resolve_stats.get("unmatched_owners", []),
        "review_queue_size": review_size,
        "auto_links": prob_stats.get("auto_links", 0),
    }


def _review_size(review_csv) -> int:
    path = Path(review_csv)
    if not path.exists():
        return 0
    with path.open() as fh:
        return max(sum(1 for _ in fh) - 1, 0)  # minus header


def _print_summary(s: dict) -> None:
    print("=== identity graph build summary ===")
    print(f"satellites:              {s['satellites']}")
    print(f"merge_log rows:          {s['merge_log_rows']}")
    print(f"probabilistic auto-links:{s['auto_links']}")
    print(f"review-queue size:       {s['review_queue_size']}")
    print("identifiers by type:")
    for id_type, count in sorted(s["identifiers_by_type"].items()):
        print(f"  {id_type:<12} {count}")
    print("assertions by source:")
    for source, count in sorted(s["assertions_by_source"].items()):
        print(f"  {source:<12} {count}")
    print(f"operator coverage:       {s['operator_coverage_pct']}%  ({s['operator_resolved']})")
    print(f"status coverage:         {s['status_coverage_pct']}%  ({s['status_resolved']})")
    print(f"unmapped status values:  {len(s['unmapped_status'])}")
    for src, val in s["unmapped_status"][:20]:
        print(f"  {src}: {val!r}")
    print(f"unmatched owner values:  {len(s['unmatched_owners'])}")
    for val in s["unmatched_owners"][:20]:
        print(f"  {val!r}")


def main() -> None:
    conn = get_conn()
    try:
        summary = run_pipeline(conn)
        conn.commit()
        _print_summary(summary)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
