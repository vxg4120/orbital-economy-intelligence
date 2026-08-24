"""Generates docs/reports/dq_report.md -- the Data Quality & Conflict Report. SPEC.md §8.

SQL against the schema (it reads only the tables the identity engine populated, never invokes the
engine) + string formatting, no plotting deps. The one identity/ import is the pure, stdlib-only
``parse_date_loose`` helper, reused so the decay-date conflict section compares *dates* rather than
raw strings (GCAT's "1957 Dec 1 1000?" and SATCAT's "1957-12-01" are the same date in different
clothes and must not read as a conflict). Safe to re-run: it always overwrites the file from
scratch.

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
from identity.normalize import parse_date_loose

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
    # Last run per (source, endpoint, status): CelesTrak serves satcat, gp AND supgp all under
    # source='celestrak', so collapsing on (source, status) alone would hide the satcat and gp
    # pulls behind whichever endpoint finished last. Keying on endpoint too keeps every pull —
    # and the skipped_fresh rows that prove the politeness gate fired — visible.
    cols, rows = _rows(
        cur,
        """
        SELECT source, endpoint, status, finished_at, rows_ingested, bytes_downloaded
        FROM (
            SELECT DISTINCT ON (source, endpoint, status)
                source, endpoint, status, finished_at, rows_ingested, bytes_downloaded
            FROM ingest_run
            ORDER BY source, endpoint, status, finished_at DESC NULLS LAST
        ) last_per_endpoint_status
        ORDER BY source, endpoint, status
        """,
    )
    return cols, rows


def _section_status_disagreements(cur):
    # Cross-source disagreement lives in source_assertion (what each source *claimed*), not in
    # satellite_status_history (which only holds the resolver's single winning status per object).
    # Each source's raw status is mapped to the canonical taxonomy via status_mapping; a
    # disagreement is two *concrete* (non-UNKNOWN) statuses that differ -- e.g. SATCAT still says
    # ACTIVE while GCAT records the object as reentered (DECAYED). Comparing to UNKNOWN would just
    # surface GCAT's silence on operational health, which is not a real conflict.
    cols, rows = _rows(
        cur,
        """
        WITH satcat AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, m.canonical_status
            FROM source_assertion a
            JOIN status_mapping m ON m.source = 'satcat' AND m.source_value = a.value
            WHERE a.source = 'satcat' AND a.attribute = 'status' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        ),
        gcat AS (
            SELECT DISTINCT ON (a.satellite_id) a.satellite_id, m.canonical_status
            FROM source_assertion a
            JOIN status_mapping m ON m.source = 'gcat' AND m.source_value = a.value
            WHERE a.source = 'gcat' AND a.attribute = 'status' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC, a.ingest_run_id DESC, a.source_key
        )
        SELECT
            s.norad_id,
            s.canonical_name,
            sc.canonical_status AS satcat_status,
            gc.canonical_status AS gcat_status
        FROM satcat sc
        JOIN gcat gc ON gc.satellite_id = sc.satellite_id
        JOIN satellite s ON s.satellite_id = sc.satellite_id
        WHERE sc.canonical_status <> gc.canonical_status
          AND sc.canonical_status <> 'UNKNOWN'
          AND gc.canonical_status <> 'UNKNOWN'
        ORDER BY s.norad_id NULLS LAST, s.satellite_id
        """,
    )
    return cols, rows


def _section_decay_date_conflicts(cur):
    # A conflict is a genuine disagreement about *when* an object decayed, so we parse each
    # source's raw value to a date and compare those -- otherwise every object would "conflict"
    # purely because GCAT writes "1957 Dec  1 1000?" where SATCAT writes "1957-12-01". The raw
    # strings are still shown in the examples so the provenance is visible. Rows arrive ordered by
    # (norad NULLS LAST, satellite_id) from SQL; dict insertion order preserves that determinism.
    cur.execute(
        """
        SELECT s.norad_id, s.canonical_name, l.satellite_id, l.source, l.value
        FROM (
            SELECT DISTINCT ON (satellite_id, source) satellite_id, source, value, observed_at
            FROM source_assertion
            WHERE attribute = 'decay_date' AND satellite_id IS NOT NULL
            ORDER BY satellite_id, source, observed_at DESC, ingest_run_id DESC, source_key
        ) l
        JOIN satellite s ON s.satellite_id = l.satellite_id
        ORDER BY s.norad_id NULLS LAST, l.satellite_id, l.source
        """
    )
    per_sat: dict = {}
    for norad, name, sat_id, source, value in cur.fetchall():
        entry = per_sat.setdefault(sat_id, {"norad": norad, "name": name, "claims": []})
        entry["claims"].append((source, value))

    cols = ["norad_id", "canonical_name", "sources_and_dates"]
    rows = []
    for entry in per_sat.values():
        parsed = {parse_date_loose(v) for _, v in entry["claims"]}
        parsed.discard(None)  # an unparseable value can't establish a date conflict
        if len(parsed) > 1:
            sources_and_dates = "; ".join(f"{s}: {v}" for s, v in entry["claims"])
            rows.append((entry["norad"], entry["name"], sources_and_dates))
    return cols, rows


def _section_stale_post_ma_owners(cur):
    cols, rows = _rows(
        cur,
        """
        WITH latest_satcat_owner AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, value AS owner_raw, observed_at
            FROM source_assertion
            WHERE attribute = 'owner' AND source = 'satcat' AND satellite_id IS NOT NULL
            ORDER BY satellite_id, observed_at DESC, ingest_run_id DESC, source_key
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


BENCHMARK_OPERATORS = (
    "SpaceX", "Eutelsat", "Planet Labs", "Spire", "Iridium", "ICEYE", "Capella Space", "Amazon",
)


def _section_phase2_metrics(cur):
    """Phase 2 benchmark metrics (SPEC §7 + the §12 killer chart), per benchmark operator.

    One row per benchmark operator: satellites carrying gp_history + elset count (attributed via the
    current SCD2 owner), median time-to-operational (v_time_to_operational_by_operator), and the p50
    30-day rolling sma-stddev station-keeping tightness (v_station_keeping_operator). Operators with
    no landed gp_history surface as 0/blank -- deliberately, so the attribution coverage gap is
    visible rather than hidden. Deterministic ORDER BY keeps the rendered report byte-stable.
    """
    per_op_cols, per_op_rows = _rows(
        cur,
        """
        WITH bench AS (
            SELECT operator_id, canonical_name FROM operator WHERE canonical_name = ANY(%s)
        ),
        hist AS (
            SELECT so.operator_id,
                   count(DISTINCT ge.norad_id) AS sats,
                   count(*) AS elsets
            FROM gp_elements ge
            JOIN satellite s ON s.norad_id = ge.norad_id
            JOIN satellite_operator so
                ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
            WHERE ge.source = 'spacetrack_gp_history'
            GROUP BY so.operator_id
        )
        SELECT
            b.canonical_name AS operator,
            COALESCE(h.sats, 0) AS sats_with_history,
            COALESCE(h.elsets, 0) AS elset_count,
            round(tto.median_days_to_operational::numeric, 1) AS median_days_to_operational,
            tto.n_satellites AS tto_n,
            round(sk.p50_station_keeping_km::numeric, 4) AS station_keeping_p50_km,
            sk.active_satellite_count AS sk_active_n
        FROM bench b
        LEFT JOIN hist h ON h.operator_id = b.operator_id
        LEFT JOIN v_time_to_operational_by_operator tto ON tto.operator_id = b.operator_id
        LEFT JOIN v_station_keeping_operator sk ON sk.operator_id = b.operator_id
        ORDER BY COALESCE(h.elsets, 0) DESC, b.canonical_name
        """,
        (list(BENCHMARK_OPERATORS),),
    )

    # Killer-chart summary: window totals for the operator with the largest attribution delta.
    # The honest showcase for this window is Eutelsat (ex-OneWeb LEO fleet, coded 'UK' by SATCAT).
    cur.execute(
        """
        SELECT
            operator_name,
            sum(temporal_sat_days)::bigint AS temporal_sat_days,
            sum(naive_satcat_sat_days)::bigint AS naive_satcat_sat_days,
            max(temporal_sats) AS temporal_sats,
            max(naive_satcat_sats) AS naive_satcat_sats
        FROM v_killer_chart
        WHERE operator_name = 'Eutelsat'
        GROUP BY operator_name
        """
    )
    killer = cur.fetchone()
    return per_op_cols, per_op_rows, killer


def _section_key_churn(cur):
    """Catalog key stability and the churn ledger (tenancy Phase 2): is the ground moving."""
    stab_cols, stab_rows = _rows(cur, """
        SELECT source, id_type, observations, referent_changes, changes_anchored,
               prev_run_id, curr_run_id
        FROM key_stability ORDER BY measured_at DESC, id_type LIMIT 10
    """)
    churn_cols, churn_rows = _rows(cur, """
        SELECT launch_key, count(*) AS churned_keys,
               count(*) FILTER (WHERE prev_anchored) AS on_anchored_rows,
               max(detected_at)::date AS latest
        FROM catalog_key_churn GROUP BY launch_key ORDER BY count(*) DESC LIMIT 10
    """)
    event_cols, event_rows = _rows(cur, """
        SELECT event, count(*) AS n, max(at)::date AS latest
        FROM identity_event GROUP BY event ORDER BY count(*) DESC
    """)
    cur.execute(
        "SELECT count(*) FROM satellite_identifier WHERE valid_to IS NOT NULL "
        "AND id_type IN ('gcat_id', 'cospar')"
    )
    expired = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM satellite WHERE anchor_state = 'provisional'")
    provisional = cur.fetchone()[0]
    return {
        "stability": (stab_cols, stab_rows),
        "churn_by_launch": (churn_cols, churn_rows),
        "events": (event_cols, event_rows),
        "expired_identifiers": expired,
        "provisional_satellites": provisional,
    }


def _section_space_weather(cur):
    """The drag environment: what the thermosphere did to the LEO fleet, and under whose Kp.

    Guarded on the metrics views existing (they come from scripts/apply_metrics.py, not a
    migration), so the report still generates on a database that has raw layers only.
    """
    cur.execute("SELECT to_regclass('v_drag_environment_daily') IS NOT NULL")
    if not cur.fetchone()[0]:
        return None
    cur.execute(
        """
        SELECT count(*) FILTER (WHERE f107_data_type = 'OBS'),
               max(day) FILTER (WHERE f107_data_type = 'OBS'),
               max(f107_obs_center81) FILTER (WHERE day = (
                   SELECT max(day) FROM v_space_weather_daily
                   WHERE f107_data_type = 'OBS' AND f107_obs_center81 IS NOT NULL))
        FROM v_space_weather_daily
        """
    )
    observed_days, observed_through, f107_81 = cur.fetchone()
    if not observed_days:
        return None
    # The coupling table: the fleet's worst drag days, with the indices that explain them.
    top_cols, top_rows = _rows(cur, """
        SELECT day, COALESCE(storm_level, '') AS storm, round(kp_max, 1) AS kp_max,
               ap_avg, sats_observed, median_dsma_m, p10_dsma_m
        FROM v_drag_environment_daily
        WHERE median_dsma_m IS NOT NULL
        ORDER BY median_dsma_m ASC LIMIT 5
    """)
    cur.execute(
        """
        SELECT
            round((percentile_cont(0.5) WITHIN GROUP (ORDER BY median_dsma_m))::numeric, 1),
            count(*) FILTER (WHERE storm_level IS NOT NULL)
        FROM v_drag_environment_daily
        WHERE median_dsma_m IS NOT NULL
        """
    )
    quiet_median, storm_days = cur.fetchone()
    # The freshest event: sharpest 3-hourly Ap peak in the last 14 observed days (peak, not
    # daily average, because a brief severe storm matters more here than a long mild one),
    # with the fleet's response on the day and on the day after: the thermosphere's density
    # response lags the storm, so the aftermath row is usually the louder one.
    cur.execute(
        """
        SELECT d.day, d.storm_level, round(d.kp_max, 1), d.ap_max, d.median_dsma_m,
               (SELECT d2.median_dsma_m FROM v_drag_environment_daily d2
                WHERE d2.day = d.day + 1) AS next_day_dsma
        FROM v_drag_environment_daily d
        WHERE d.f107_data_type = 'OBS' AND d.day >= (
            SELECT max(day) FROM v_space_weather_daily WHERE f107_data_type = 'OBS'
        ) - 14
        ORDER BY d.ap_max DESC NULLS LAST, d.day DESC LIMIT 1
        """
    )
    fresh = cur.fetchone()
    return {
        "observed_days": observed_days,
        "observed_through": observed_through,
        "f107_center81": f107_81,
        "top": (top_cols, top_rows),
        "overall_median_dsma": quiet_median,
        "storm_days_in_window": storm_days,
        "freshest": fresh,
    }


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0%"
    return f"{100.0 * numerator / denominator:.1f}%"


# ---------------------------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------------------------


def _section_filing_specs(cur):
    """The pre-launch pipeline: pending FCC filings, harvested documents, extracted specs.

    Guarded on the 0020/0021 objects existing, so the report still generates on a database that
    has raw layers only. Validation-rate numbers separate served from stored on purpose: rows
    that fail page-level validation are kept for debugging and never served, and the gap between
    the two is the honest health signal for the extraction layer.
    """
    cur.execute("SELECT to_regclass('fcc_spec_filing') IS NOT NULL AND "
                "to_regclass('fcc_filing_blob') IS NOT NULL")
    if not cur.fetchone()[0]:
        return None
    cur.execute(
        """
        SELECT
          (SELECT count(*) FROM v_fcc_pending_applications),
          (SELECT count(DISTINCT file_number) FROM fcc_filing_document),
          (SELECT count(*) FROM fcc_filing_document),
          (SELECT count(*) FROM fcc_spec_filing WHERE is_validated),
          (SELECT count(*) FROM fcc_spec_orbital WHERE is_validated),
          (SELECT count(*) FROM fcc_spec_orbital),
          (SELECT count(*) FROM fcc_spec_band WHERE is_validated),
          (SELECT count(*) FROM fcc_spec_band),
          (SELECT count(*) FROM fcc_filing_blob WHERE fetch_status = 'ok'),
          (SELECT count(*) FROM fcc_filing_blob WHERE fetch_status = 'truncated'),
          (SELECT count(DISTINCT file_number) FROM fcc_filing_blob
            WHERE fetch_status = 'truncated'),
          (SELECT count(*) FROM fcc_spec_orbital WHERE is_validated
            AND apogee_km IS NOT NULL AND apogee_km NOT BETWEEN 150 AND 50000),
          (SELECT count(*) FROM v_fcc_docket),
          (SELECT count(*) FROM v_fcc_docket WHERE filings_total > 1)
        """
    )
    (pending, filings_with_docs, doc_rows, spec_filings, planes_ok, planes_all,
     bands_ok, bands_all, blobs_ok, blobs_trunc, trunc_filings, lunar,
     dockets, multi_dockets) = cur.fetchone()
    return {
        "pending": pending, "filings_with_docs": filings_with_docs, "doc_rows": doc_rows,
        "spec_filings": spec_filings, "planes_ok": planes_ok, "planes_all": planes_all,
        "bands_ok": bands_ok, "bands_all": bands_all, "blobs_ok": blobs_ok,
        "blobs_trunc": blobs_trunc, "trunc_filings": trunc_filings, "lunar": lunar,
        "dockets": dockets, "multi_dockets": multi_dockets,
    }


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
        p2_cols, p2_rows, p2_killer = _section_phase2_metrics(cur)
        key_churn = _section_key_churn(cur)
        space_weather = _section_space_weather(cur)
        filing_specs = _section_filing_specs(cur)

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

    out.append("\n## 6. Catalog key stability and churn\n")
    out.append(
        "\nProvisional catalog keys move on fresh launches; these are measurements, not "
        "assumptions. A referent change means the same key named a different object in the "
        "next snapshot (compared by normalized name).\n"
    )
    out.append("\n### Key stability, latest comparisons\n")
    out.append(_md_table(*key_churn["stability"]))
    out.append("\n### Churn by launch\n")
    out.append(_md_table(*key_churn["churn_by_launch"]))
    out.append("\n### Identity events\n")
    out.append(_md_table(*key_churn["events"]))
    out.append(
        f"\nExpired volatile identifiers: **{key_churn['expired_identifiers']}** -- "
        f"satellites still provisional: **{key_churn['provisional_satellites']}**\n"
    )

    out.append("\n## 7. Coverage\n")
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

    out.append("\n## 8. Phase 2 metrics (per benchmark operator)\n")
    out.append(
        "\nSPEC §7 metrics over the gp_history backfill. `sats_with_history`/`elset_count` are "
        "attributed via the current SCD2 owner; `median_days_to_operational` is over in-window LEO "
        "launches that acquired their shell band (v_time_to_operational); `station_keeping_p50_km` "
        "is the p50 of per-satellite median 30-day rolling sma-stddev for ACTIVE payloads. Blank = "
        "no landed gp_history for that operator (see the attribution note below).\n\n"
    )
    out.append(_md_table(p2_cols, p2_rows))
    if p2_killer is not None:
        op, t_days, n_days, t_sats, n_sats = p2_killer
        ratio = (t_days / n_days) if n_days else None
        ratio_txt = f"{ratio:.1f}x" if ratio is not None else "n/a (naive attributes 0)"
        out.append(
            f"\n### Killer chart (SPEC §12): temporal vs naive-SATCAT attribution -- {op}\n\n"
            "SATCAT's OWNER field is a country/agency code, not a company: the ex-OneWeb LEO fleet "
            "is coded 'UK' (maps to no operator) while only Eutelsat's legacy birds carry 'EUTE'. "
            "Temporal identity resolution assigns the whole fleet to its actual current operator; "
            "naive SATCAT owner codes cannot.\n\n"
            f"- Temporal (SCD2) attribution: **{t_sats}** sats, **{t_days:,}** elset-days.\n"
            f"- Naive SATCAT owner code: **{n_sats}** sats, **{n_days:,}** elset-days.\n"
            f"- Delta: **{t_sats - n_sats}** sats / **{t_days - n_days:,}** elset-days "
            f"({ratio_txt} more elset-days attributed under temporal resolution).\n"
        )

    out.append("\n## 9. Space weather and the drag environment\n")
    if space_weather is None:
        out.append(
            "\nNo space-weather data landed yet (run `scripts/ingest_all.py --source sw` and "
            "`scripts/apply_metrics.py`).\n"
        )
    else:
        sw = space_weather
        out.append(
            f"\nCelesTrak consolidated indices: **{sw['observed_days']:,}** observed days, "
            f"through **{sw['observed_through']}**; F10.7 81-day centered mean at the last "
            f"observed day: **{sw['f107_center81']}** sfu.\n"
        )
        out.append(
            "\nThe coupling this section exists to measure: geomagnetic storms heat the "
            "thermosphere and the LEO fleet's semi-major axes respond within a day or two. "
            "`median_dsma_m` is the fleet-wide median one-day SMA change over LEO payload "
            "observations (consecutive-day pairs only, 10 km/day glitch clamp, days under 500 "
            "pairs withheld), so it is robust to individual maneuvers: when it moves, the "
            "atmosphere moved everyone.\n"
        )
        out.append("\n### Worst fleet drag days in the behavior window\n\n")
        out.append(_md_table(*sw["top"]))
        out.append(
            f"\nAll-window median of the daily fleet median: **{sw['overall_median_dsma']} "
            f"m/day**; geomagnetic-storm days (G1 or stronger) inside the window: "
            f"**{sw['storm_days_in_window']}**. Storm days and their one-to-two-day aftermath "
            "dominate the worst-drag table; the lag is the thermosphere's density response "
            "time, visible directly in the data.\n"
        )
        if sw["freshest"] is not None:
            day, level, kp, ap_max, dsma, next_dsma = sw["freshest"]
            level_txt = level or "below G1"
            parts = []
            if dsma is not None:
                parts.append(f"fleet median {dsma} m/day on the day")
            if next_dsma is not None:
                parts.append(f"{next_dsma} m/day the day after")
            dsma_txt = ("; ".join(parts) if parts
                        else "no published drag rows around it yet")
            out.append(
                f"\n### Freshest event\n\nSharpest 3-hourly Ap peak in the last 14 observed "
                f"days: **{day}** (peak Ap {ap_max}, Kp max {kp}, {level_txt}); {dsma_txt}. "
                "The day-after number is usually the louder one: thermospheric density "
                "responds with a lag.\n"
            )

    out.append("\n## 10. FCC filings and extracted specs\n")
    if filing_specs is None:
        out.append(
            "\nNo filings layer yet (migrations 0018/0020/0021 not applied, or the IBFS ingest "
            "has not run).\n"
        )
    else:
        fs = filing_specs
        out.append(
            f"\nPending space-station applications: **{fs['pending']:,}**; "
            f"**{fs['filings_with_docs']}** filings carry harvested document inventories "
            f"(**{fs['doc_rows']:,}** documents). Validated Schedule S extractions serve "
            f"**{fs['spec_filings']}** filings: **{fs['planes_ok']}/{fs['planes_all']}** orbital "
            f"planes and **{fs['bands_ok']}/{fs['bands_all']}** frequency bands validated "
            "against the pages they cite; rows that fail validation are stored and never "
            "served, so a widening gap here is the extraction layer's health alarm.\n"
        )
        out.append(
            f"\nBlob cache: **{fs['blobs_ok']}** documents fetched whole, "
            f"**{fs['blobs_trunc']}** truncated server-side by the FCC gateway (all in "
            f"**{fs['trunc_filings']}** filing(s)) -- a published coverage gap, not a parse "
            f"failure. **{fs['lunar']}** plane rows carry as-filed lunar sentinels, served "
            "flagged and excluded from summary ranges. Dockets: "
            f"**{fs['dockets']:,}** callsign groups, **{fs['multi_dockets']}** holding more "
            "than one filing; dockets are timelines, never supersession chains.\n"
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
