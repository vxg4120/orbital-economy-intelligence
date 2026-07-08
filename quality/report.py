"""Generates docs/reports/dq_report.md -- the Data Quality & Conflict Report. SPEC.md §8.

Pure SQL against the schema (no import of identity/ -- this module must work standalone against
whatever the identity engine has written to the dimension tables) + string formatting, no
plotting deps. Safe to re-run: it always overwrites the target file from scratch.

Determinism: every query below has an explicit ORDER BY so that, given the same underlying data,
the generated markdown is byte-identical except for the "generated at" header timestamp -- making
diffs of the committed report reviewable.

Two entry points:
  - generate_report(conn) -> str: pure function, returns markdown text. Tests call this directly
    against their own (possibly uncommitted) db_conn transaction so seeded fixture rows are
    visible without a commit.
  - main(): the `python quality/report.py` / `make report` entry point -- opens its own
    connection and writes docs/reports/dq_report.md.
"""

import csv
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.db import get_conn

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = REPO_ROOT / "docs" / "reports" / "dq_report.md"
REVIEW_QUEUE_CSV = REPO_ROOT / "data" / "review" / "match_review.csv"

EXAMPLE_LIMIT = 10


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d.name for d in cur.description]
    return cols, cur.fetchall()


def _fmt_cell(v):
    if v is None:
        return ""
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _md_table(cols, rows):
    if not rows:
        return "_(none)_\n"
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt_cell(v) for v in row) + " |")
    return "\n".join(lines) + "\n"


def _review_queue_size() -> int:
    """Row count (minus header) of data/review/match_review.csv, or 0 if it doesn't exist yet.

    That file is written by identity/match.py's probabilistic pass for borderline (0.75-0.92)
    matches; it is gitignored (lives under data/) and may not exist before the identity build has
    run at all.
    """
    if not REVIEW_QUEUE_CSV.exists():
        return 0
    with REVIEW_QUEUE_CSV.open(newline="") as f:
        row_count = sum(1 for _ in csv.reader(f))
    return max(row_count - 1, 0)


# ---------------------------------------------------------------------------------------------
# Section queries
# ---------------------------------------------------------------------------------------------


def _section_header(cur):
    cols, rows = _rows(
        cur,
        """
        SELECT source, endpoint, status, finished_at, rows_ingested, bytes_downloaded
        FROM (
            SELECT DISTINCT ON (source, status)
                source, endpoint, status, finished_at, rows_ingested, bytes_downloaded
            FROM ingest_run
            ORDER BY source, status, finished_at DESC NULLS LAST
        ) last_per_source_status
        ORDER BY source, status
        """,
    )
    return cols, rows


def _section_status_disagreements(cur):
    cols, rows = _rows(
        cur,
        """
        WITH latest AS (
            SELECT DISTINCT ON (satellite_id, source)
                satellite_id, source, canonical_status, observed_at
            FROM satellite_status_history
            WHERE source IN ('satcat', 'gcat')
            ORDER BY satellite_id, source, observed_at DESC
        ),
        satcat AS (SELECT satellite_id, canonical_status FROM latest WHERE source = 'satcat'),
        gcat   AS (SELECT satellite_id, canonical_status FROM latest WHERE source = 'gcat')
        SELECT
            s.norad_id,
            s.canonical_name,
            sc.canonical_status AS satcat_status,
            gc.canonical_status AS gcat_status
        FROM satcat sc
        JOIN gcat gc ON gc.satellite_id = sc.satellite_id
        JOIN satellite s ON s.satellite_id = sc.satellite_id
        WHERE sc.canonical_status IS DISTINCT FROM gc.canonical_status
        ORDER BY s.norad_id NULLS LAST, s.satellite_id
        """,
    )
    return cols, rows


def _section_decay_date_conflicts(cur):
    cols, rows = _rows(
        cur,
        """
        WITH latest AS (
            SELECT DISTINCT ON (satellite_id, source)
                satellite_id, source, value, observed_at
            FROM source_assertion
            WHERE attribute = 'decay_date' AND satellite_id IS NOT NULL
            ORDER BY satellite_id, source, observed_at DESC
        ),
        conflicting AS (
            SELECT satellite_id,
                   string_agg(source || ': ' || value, '; ' ORDER BY source) AS sources_and_dates
            FROM latest
            GROUP BY satellite_id
            HAVING count(DISTINCT value) > 1
        )
        SELECT s.norad_id, s.canonical_name, c.sources_and_dates
        FROM conflicting c
        JOIN satellite s ON s.satellite_id = c.satellite_id
        ORDER BY s.norad_id NULLS LAST, s.satellite_id
        """,
    )
    return cols, rows


def _section_stale_post_ma_owners(cur):
    cols, rows = _rows(
        cur,
        """
        WITH latest_satcat_owner AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, value AS owner_raw, observed_at
            FROM source_assertion
            WHERE attribute = 'owner' AND source = 'satcat' AND satellite_id IS NOT NULL
            ORDER BY satellite_id, observed_at DESC
        ),
        owner_operator AS (
            SELECT lso.satellite_id, lso.owner_raw, oa.operator_id
            FROM latest_satcat_owner lso
            JOIN operator_alias oa
                ON oa.source = 'satcat' AND lower(oa.alias) = lower(lso.owner_raw)
        )
        SELECT
            s.norad_id,
            s.canonical_name,
            oo.owner_raw AS satcat_owner_code,
            o_child.canonical_name AS resolved_to_child,
            o_parent.canonical_name AS should_be_parent,
            orl.relationship,
            orl.valid_from AS relationship_since
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
        """,
    )
    return cols, rows


def _section_supgp_cross_tags(cur):
    cur.execute("SELECT count(*) FROM raw_supgp_status")
    total = cur.fetchone()[0]
    cols, rows = _rows(
        cur,
        """
        SELECT norad_id, object_name, file_tag, flag, detail
        FROM raw_supgp_status
        ORDER BY raw_supgp_status_id
        """,
    )
    return total, cols, rows


def _section_match_merge_stats(cur):
    by_id_type_cols, by_id_type_rows = _rows(
        cur,
        "SELECT id_type, count(*) AS crosswalk_rows FROM satellite_identifier "
        "GROUP BY id_type ORDER BY id_type",
    )
    by_rule_cols, by_rule_rows = _rows(
        cur,
        "SELECT rule_fired, count(*) AS merges FROM merge_log "
        "GROUP BY rule_fired ORDER BY rule_fired",
    )
    unmatched_cols, unmatched_rows = _rows(
        cur,
        "SELECT source, count(DISTINCT source_key) AS unmatched_objects FROM source_assertion "
        "WHERE satellite_id IS NULL GROUP BY source ORDER BY source",
    )
    review_queue_size = _review_queue_size()
    return {
        "by_id_type": (by_id_type_cols, by_id_type_rows),
        "by_rule": (by_rule_cols, by_rule_rows),
        "unmatched": (unmatched_cols, unmatched_rows),
        "review_queue_size": review_queue_size,
    }


def _section_coverage(cur):
    cur.execute(
        """
        WITH latest_status AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
            FROM satellite_status_history
            ORDER BY satellite_id, observed_at DESC
        ),
        on_orbit AS (
            SELECT s.satellite_id
            FROM satellite s
            LEFT JOIN latest_status ls ON ls.satellite_id = s.satellite_id
            WHERE s.object_type = 'PAYLOAD'
              AND COALESCE(ls.canonical_status, 'UNKNOWN') != 'DECAYED'
        ),
        with_operator AS (
            SELECT DISTINCT satellite_id FROM satellite_operator
            WHERE role = 'owner' AND valid_to IS NULL
        ),
        with_status AS (
            SELECT satellite_id FROM latest_status WHERE canonical_status != 'UNKNOWN'
        ),
        id_counts AS (
            SELECT satellite_id, count(*) AS n_ids FROM satellite_identifier
            GROUP BY satellite_id
        )
        SELECT
            (SELECT count(*) FROM on_orbit) AS total_on_orbit,
            (SELECT count(*) FROM on_orbit oo JOIN with_operator wo
                ON wo.satellite_id = oo.satellite_id) AS with_operator_count,
            (SELECT count(*) FROM on_orbit oo JOIN with_status ws
                ON ws.satellite_id = oo.satellite_id) AS with_status_count,
            (SELECT count(*) FROM on_orbit oo JOIN id_counts ic
                ON ic.satellite_id = oo.satellite_id WHERE ic.n_ids >= 2) AS with_2plus_ids
        """
    )
    total, with_operator, with_status, with_2plus_ids = cur.fetchone()
    return {
        "total_on_orbit": total,
        "with_operator": with_operator,
        "with_status": with_status,
        "with_2plus_ids": with_2plus_ids,
    }


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0%"
    return f"{100.0 * numerator / denominator:.1f}%"


# ---------------------------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------------------------


def generate_report(conn) -> str:
    """Build the full markdown report against the given open connection.

    Read-only: issues SELECTs only, never commits/rolls back the caller's transaction (so tests
    can call this against an uncommitted fixture and roll back afterwards).
    """
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out = []

    with conn.cursor() as cur:
        header_cols, header_rows = _section_header(cur)
        status_cols, status_rows = _section_status_disagreements(cur)
        decay_cols, decay_rows = _section_decay_date_conflicts(cur)
        stale_cols, stale_rows = _section_stale_post_ma_owners(cur)
        supgp_total, supgp_cols, supgp_rows = _section_supgp_cross_tags(cur)
        match_merge = _section_match_merge_stats(cur)
        coverage = _section_coverage(cur)

    out.append("# Data Quality and Conflict Report\n")
    out.append(f"Generated at: {now}\n")
    out.append(
        "\nEvery number below comes from a live query against the identity graph and fact "
        "layer -- disagreements are data, not errors (SPEC.md §8).\n"
    )

    out.append("\n## Ingestion ledger: last run per source/status\n")
    out.append(_md_table(header_cols, header_rows))

    out.append("\n## 1. Status disagreements: SATCAT vs GCAT\n")
    out.append(f"\nCount: **{len(status_rows)}**\n\n")
    out.append(_md_table(status_cols, status_rows[:EXAMPLE_LIMIT]))

    out.append("\n## 2. Decay-date conflicts across sources\n")
    out.append(f"\nCount: **{len(decay_rows)}**\n\n")
    out.append(_md_table(decay_cols, decay_rows[:EXAMPLE_LIMIT]))

    out.append("\n## 3. Stale post-M&A owners\n")
    out.append(
        "\nSatellites whose latest SATCAT owner assertion still resolves to a company that has "
        "since been acquired or merged (e.g. OneWeb -> Eutelsat, Inmarsat -> Viasat, "
        "Intelsat -> SES) -- the catalog still names the child.\n\n"
    )
    out.append(f"Count: **{len(stale_rows)}**\n\n")
    out.append(_md_table(stale_cols, stale_rows[:EXAMPLE_LIMIT]))

    out.append("\n## 4. SupGP cross-tag anomalies\n")
    if supgp_total == 0:
        out.append("\nNo data yet.\n")
    else:
        out.append(f"\nCount: **{supgp_total}**\n\n")
        out.append(_md_table(supgp_cols, supgp_rows[:EXAMPLE_LIMIT]))

    out.append("\n## 5. Match/merge stats\n")
    out.append("\n### Crosswalk rows by id_type\n")
    out.append(_md_table(*match_merge["by_id_type"]))
    out.append("\n### merge_log by rule_fired\n")
    out.append(_md_table(*match_merge["by_rule"]))
    out.append(f"\n### Review-queue size: **{match_merge['review_queue_size']}**\n")
    out.append("\n### Unmatched objects by source (source_assertion.satellite_id IS NULL)\n")
    out.append(_md_table(*match_merge["unmatched"]))

    out.append("\n## 6. Coverage\n")
    total = coverage["total_on_orbit"]
    out.append(f"\nOn-orbit payloads (PAYLOAD, latest status != DECAYED): **{total}**\n\n")
    out.append(
        f"- With resolved operator: {coverage['with_operator']}/{total} "
        f"({_pct(coverage['with_operator'], total)})\n"
    )
    out.append(
        f"- With non-UNKNOWN status: {coverage['with_status']}/{total} "
        f"({_pct(coverage['with_status'], total)})\n"
    )
    out.append(
        f"- With >=2 source identifiers (graph vs list): {coverage['with_2plus_ids']}/{total} "
        f"({_pct(coverage['with_2plus_ids'], total)})\n"
    )

    return "".join(out)


def write_report(conn, path: pathlib.Path = DEFAULT_REPORT_PATH) -> pathlib.Path:
    content = generate_report(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def main() -> None:
    conn = get_conn()
    try:
        path = write_report(conn)
        print(f"wrote {path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
