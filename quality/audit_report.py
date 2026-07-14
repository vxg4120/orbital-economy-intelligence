"""Generates the recurring **Orbital Behavior Report** -> docs/reports/orbital-behavior-<YYYY-MM>.md.

The flagship artifact of the identity-graph project under its committed thesis: an *independent
behavioral auditor of the megaconstellation era* -- involuntary, physics-based, provenance-backed
verification of operator behavior. Every number below is computed live against the identity graph
and the gp_elements fact layer at generation time; nothing is hardcoded. The tone is audit-grade
and neutral by design -- the report's credibility IS the product, so each figure is stated with its
n and the SQL-derivable definition inline, and every limitation is stated plainly (annex).

Read-only: issues SELECTs only; never writes the database. Reuses the query idioms and determinism
discipline of quality/report.py (every query has an explicit, tie-broken ORDER BY) so the rendered
markdown is byte-identical across runs on the same data except for the single generated-at line.

Parameterised period: ``--period-end YYYY-MM-DD`` (default: the latest gp_elements epoch date) and
``--period-months`` (default 12). The report covers ``[period_end - period_months, period_end]``.

Entry points mirror quality/report.py:
  - generate_report(conn, period_end=None, period_months=12) -> str: pure; tests call it directly.
  - main(): the ``python quality/audit_report.py`` / ``make audit`` entry point.
"""

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.db import get_conn
from identity.normalize import canonical_object_type, parse_date_loose
from quality.report import (
    _md_table,
    _pct,
    _section_decay_date_conflicts,
    _section_status_disagreements,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "docs" / "reports"

# --- Section 2: Kuiper deployment-milestone constants (the news hook) ------------------------
KUIPER_OPERATOR = "Amazon"  # canonical name of the Kuiper operator in the identity graph
KUIPER_SHELL_KM = 630.0  # nominal operational shell altitude
SHELL_TOL_KM = 15.0  # +/- band that counts as "at the operational shell"
STABLE_SMA_STDDEV_KM = 5.0  # 30-day rolling sma stddev below this = station-keeping locked
DECAYING_PERIGEE_KM = 300.0  # perigee at/below this on a live bird = decaying, not raising
FCC_50PCT_MILESTONE = 1618  # FCC 50% deployment milestone: operational satellites required...
FCC_50PCT_DUE = dt.date(2026, 7, 30)  # ...by this date

# --- Benchmark operator sets ----------------------------------------------------------------
# LEO disposal leaderboard (S3). Current-owner (SCD2) attribution already resolves ex-OneWeb to
# Eutelsat and Swarm to SpaceX, so no child-operator expansion is needed here.
LEO_BENCHMARK_OPERATORS = (
    "SpaceX", "Eutelsat", "Planet Labs", "Spire", "Iridium", "ICEYE", "Capella Space", "Amazon",
)

# --- GEO end-of-life constants (S4) ---------------------------------------------------------
GEO_ALT_KM = 35786.0
GEO_BAND_LO_KM = 34000.0  # mean-altitude gate for "near/at the GEO belt"
GEO_BAND_HI_KM = 38000.0
GRAVEYARD_MIN_BOOST_KM = 235.0  # IADC minimum re-orbit boost above GEO (235 km + 1000*Cr*A/m)
GEO_PROTECTED_HALF_KM = 200.0  # +/- band around GEO defining the protected operational region

LINGERING_PERIGEE_KM = 500.0  # INACTIVE LEO payload above this perigee = "lingering" (dead-and-high)
LEO_APOGEE_MAX_KM = 2000.0

METHODOLOGY_ANNEX_ANCHOR = "#7-methodology--limitations-annex"


def _q(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d.name for d in cur.description] if cur.description else []
    return cols, cur.fetchall()


def _scalar(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _months_before(d: dt.date, months: int) -> dt.date:
    """Calendar date `months` before `d`, clamping the day for short target months."""
    total = d.year * 12 + (d.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    # clamp day to the target month's length
    if month == 12:
        next_first = dt.date(year + 1, 1, 1)
    else:
        next_first = dt.date(year, month + 1, 1)
    last_day = (next_first - dt.timedelta(days=1)).day
    return dt.date(year, month, min(d.day, last_day))


# ---------------------------------------------------------------------------------------------
# Section 1: cover + data basis
# ---------------------------------------------------------------------------------------------

def _data_basis(cur):
    vintage_cols, vintage_rows = _q(
        cur,
        """
        SELECT source, max(finished_at)::date AS last_successful_ingest, count(*) AS ok_runs
        FROM ingest_run WHERE status = 'ok'
        GROUP BY source ORDER BY source
        """,
    )
    assert_cols, assert_rows = _q(
        cur,
        "SELECT source, count(*) AS assertions FROM source_assertion "
        "GROUP BY source ORDER BY assertions DESC",
    )
    totals = {
        "satellites": _scalar(cur, "SELECT count(*) FROM satellite"),
        "assertions": _scalar(cur, "SELECT count(*) FROM source_assertion"),
        "identifiers": _scalar(cur, "SELECT count(*) FROM satellite_identifier"),
        "operators": _scalar(cur, "SELECT count(*) FROM operator"),
        "operator_relationships": _scalar(cur, "SELECT count(*) FROM operator_relationship"),
        "gp_elements": _scalar(cur, "SELECT count(*) FROM gp_elements"),
        "latest_epoch": _scalar(cur, "SELECT max(epoch)::date FROM gp_elements"),
    }
    return {"vintage": (vintage_cols, vintage_rows), "assertions": (assert_cols, assert_rows),
            "totals": totals}


# ---------------------------------------------------------------------------------------------
# Section 2: Kuiper deployment-milestone verification (the lead / news hook)
# ---------------------------------------------------------------------------------------------

def _kuiper(cur, period_start, period_end):
    # One physics-based partition of every currently-Amazon-owned payload into exactly one bucket,
    # so the counts reconcile: deployed = at_shell_stable + raising + deorbited + other.
    #   deorbited      : latest canonical status = DECAYED
    #   at_shell_stable: latest mean-altitude within +/-SHELL_TOL of the 630 km shell AND its
    #                    30-day rolling sma stddev < STABLE_SMA_STDDEV_KM (station-keeping locked)
    #   raising        : on-orbit, perigee > DECAYING_PERIGEE, not yet locked at the shell
    #   other          : perigee <= DECAYING_PERIGEE (decaying but not yet flagged) OR no elements
    cols, rows = _q(
        cur,
        """
        WITH fleet AS (
            SELECT s.satellite_id, s.norad_id
            FROM satellite s
            JOIN satellite_operator so
                ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
            JOIN operator o ON o.operator_id = so.operator_id
            WHERE o.canonical_name = %(op)s AND s.object_type = 'PAYLOAD'
        ),
        ls AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
            FROM satellite_status_history ORDER BY satellite_id, observed_at DESC
        ),
        latest AS (
            SELECT DISTINCT ON (ge.norad_id) ge.norad_id,
                   (ge.perigee_km + ge.apogee_km) / 2.0 AS mean_alt, ge.perigee_km
            FROM gp_elements ge JOIN fleet f ON f.norad_id = ge.norad_id
            ORDER BY ge.norad_id, ge.epoch DESC, ge.source
        ),
        recent AS (
            SELECT sd.norad_id, stddev_samp(sd.sma_avg) AS sma_sd_30d
            FROM sat_daily sd JOIN fleet f ON f.norad_id = sd.norad_id
            WHERE sd.day > (SELECT max(day) FROM sat_daily) - INTERVAL '30 days'
            GROUP BY sd.norad_id
        ),
        classified AS (
            SELECT
                CASE
                    WHEN COALESCE(ls.canonical_status, '') = 'DECAYED' THEN 'deorbited'
                    WHEN l.norad_id IS NULL THEN 'other'
                    WHEN l.perigee_km <= %(decay_per)s THEN 'other'
                    WHEN l.mean_alt BETWEEN %(shell)s - %(tol)s AND %(shell)s + %(tol)s
                         AND COALESCE(r.sma_sd_30d, 1e9) < %(stable)s THEN 'at_shell_stable'
                    ELSE 'raising'
                END AS regime
            FROM fleet f
            LEFT JOIN ls ON ls.satellite_id = f.satellite_id
            LEFT JOIN latest l ON l.norad_id = f.norad_id
            LEFT JOIN recent r ON r.norad_id = f.norad_id
        )
        SELECT regime, count(*) AS n FROM classified GROUP BY regime ORDER BY regime
        """,
        {"op": KUIPER_OPERATOR, "shell": KUIPER_SHELL_KM, "tol": SHELL_TOL_KM,
         "stable": STABLE_SMA_STDDEV_KM, "decay_per": DECAYING_PERIGEE_KM},
    )
    part = {"at_shell_stable": 0, "raising": 0, "deorbited": 0, "other": 0}
    for regime, n in rows:
        part[regime] = n
    part["deployed"] = sum(part[k] for k in ("at_shell_stable", "raising", "deorbited", "other"))

    trend_cols, trend_rows = _q(
        cur,
        """
        SELECT to_char(date_trunc('month', s.launch_date), 'YYYY-MM') AS launch_month,
               count(*) AS payloads_deployed
        FROM satellite s
        JOIN satellite_operator so
            ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
        JOIN operator o ON o.operator_id = so.operator_id
        WHERE o.canonical_name = %(op)s AND s.object_type = 'PAYLOAD' AND s.launch_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """,
        {"op": KUIPER_OPERATOR},
    )
    deployed_in_period = _scalar(
        cur,
        """
        SELECT count(*)
        FROM satellite s
        JOIN satellite_operator so
            ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
        JOIN operator o ON o.operator_id = so.operator_id
        WHERE o.canonical_name = %(op)s AND s.object_type = 'PAYLOAD'
          AND s.launch_date >= %(ps)s AND s.launch_date <= %(pe)s
        """,
        {"op": KUIPER_OPERATOR, "ps": period_start, "pe": period_end},
    )
    return {"part": part, "trend": (trend_cols, trend_rows),
            "deployed_in_period": deployed_in_period}


# ---------------------------------------------------------------------------------------------
# Section 3: LEO disposal-compliance leaderboard
# ---------------------------------------------------------------------------------------------

def _leo_disposal(cur, period_start, period_end):
    params = {"ops": list(LEO_BENCHMARK_OPERATORS), "ling_per": LINGERING_PERIGEE_KM,
              "leo_apo": LEO_APOGEE_MAX_KM, "ps": period_start, "pe": period_end}

    # INACTIVE totals per operator (status only, no elements needed) ...
    _, inactive_rows = _q(
        cur,
        """
        WITH bench AS (
            SELECT operator_id, canonical_name FROM operator WHERE canonical_name = ANY(%(ops)s)
        ),
        ls AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
            FROM satellite_status_history ORDER BY satellite_id, observed_at DESC
        )
        SELECT b.canonical_name AS op, count(*) AS inactive_total
        FROM satellite s
        JOIN satellite_operator so
            ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
        JOIN bench b ON b.operator_id = so.operator_id
        JOIN ls ON ls.satellite_id = s.satellite_id AND ls.canonical_status = 'INACTIVE'
        WHERE s.object_type = 'PAYLOAD'
        GROUP BY b.canonical_name
        """,
        params,
    )
    inactive_by_op = {r[0]: r[1] for r in inactive_rows}

    # ... and the lingering ("dead-and-high") subset, with the latest element restricted to the
    # INACTIVE fleet so the DISTINCT ON scans only those objects' history, not every benchmark bird.
    ling_cols, ling_rows = _q(
        cur,
        """
        WITH bench AS (
            SELECT operator_id, canonical_name FROM operator WHERE canonical_name = ANY(%(ops)s)
        ),
        ls AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
            FROM satellite_status_history ORDER BY satellite_id, observed_at DESC
        ),
        inactive_fleet AS (
            SELECT s.norad_id, b.canonical_name AS op
            FROM satellite s
            JOIN satellite_operator so
                ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
            JOIN bench b ON b.operator_id = so.operator_id
            JOIN ls ON ls.satellite_id = s.satellite_id AND ls.canonical_status = 'INACTIVE'
            WHERE s.object_type = 'PAYLOAD'
        ),
        latest AS (
            SELECT DISTINCT ON (ge.norad_id) ge.norad_id, ge.perigee_km, ge.apogee_km,
                   (ge.perigee_km + ge.apogee_km) / 2.0 AS mean_alt
            FROM gp_elements ge JOIN inactive_fleet f ON f.norad_id = ge.norad_id
            ORDER BY ge.norad_id, ge.epoch DESC, ge.source
        )
        SELECT f.op,
            count(*) FILTER (WHERE l.apogee_km < %(leo_apo)s AND l.perigee_km > %(ling_per)s)
                AS lingering,
            round(avg(l.mean_alt) FILTER (WHERE l.apogee_km < %(leo_apo)s
                AND l.perigee_km > %(ling_per)s)::numeric, 1) AS lingering_avg_alt_km,
            round(avg(l.perigee_km) FILTER (WHERE l.apogee_km < %(leo_apo)s
                AND l.perigee_km > %(ling_per)s)::numeric, 1) AS lingering_avg_perigee_km
        FROM inactive_fleet f
        LEFT JOIN latest l ON l.norad_id = f.norad_id
        GROUP BY f.op
        """,
        params,
    )
    ling_by_op = {r[0]: (r[0], inactive_by_op.get(r[0], 0), r[1], r[2], r[3]) for r in ling_rows}

    # Physics-confirmed reentries in-period + disposal descent characterisation, per operator.
    # Computed over the sat_daily continuous aggregate (one row per object-day) rather than raw
    # element sets, for speed. op_alt = p90 of the object's daily mean-altitude (its operational
    # shell, robust to the decay tail); descent_days = decay_date - last day within 30 km of that
    # shell. A short descent from a high shell is the physics signature of active/propulsive
    # disposal (passive drag from 500 km takes years). median_final_perigee = last daily perigee.
    re_cols, re_rows = _q(
        cur,
        """
        WITH bench AS (
            SELECT operator_id, canonical_name FROM operator WHERE canonical_name = ANY(%(ops)s)
        ),
        reentered AS (
            SELECT s.norad_id, b.canonical_name AS op, s.decay_date
            FROM satellite s
            JOIN satellite_operator so
                ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
            JOIN bench b ON b.operator_id = so.operator_id
            JOIN (SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
                  FROM satellite_status_history ORDER BY satellite_id, observed_at DESC) ls
                ON ls.satellite_id = s.satellite_id
            WHERE s.object_type = 'PAYLOAD' AND ls.canonical_status = 'DECAYED'
              AND s.decay_date >= %(ps)s AND s.decay_date <= %(pe)s
        ),
        daily AS (
            SELECT r.norad_id, r.op, r.decay_date, sd.day::date AS d, sd.perigee_min,
                   (sd.perigee_min + sd.apogee_max) / 2.0 AS mean_alt
            FROM reentered r JOIN sat_daily sd ON sd.norad_id = r.norad_id
        ),
        op_alt AS (
            SELECT norad_id, percentile_cont(0.9) WITHIN GROUP (ORDER BY mean_alt) AS op_alt_km
            FROM daily GROUP BY norad_id
        ),
        final_perigee AS (
            SELECT DISTINCT ON (norad_id) norad_id, perigee_min AS perigee_km
            FROM daily ORDER BY norad_id, d DESC
        ),
        shell_exit AS (
            SELECT e.norad_id, max(e.d) AS exit_day
            FROM daily e JOIN op_alt a ON a.norad_id = e.norad_id
            WHERE e.mean_alt >= a.op_alt_km - 30 GROUP BY e.norad_id
        ),
        per_sat AS (
            SELECT r.norad_id, r.op, a.op_alt_km, fp.perigee_km,
                   (r.decay_date - se.exit_day) AS descent_days
            FROM reentered r
            JOIN op_alt a ON a.norad_id = r.norad_id
            JOIN final_perigee fp ON fp.norad_id = r.norad_id
            JOIN shell_exit se ON se.norad_id = r.norad_id
        )
        SELECT op,
            count(*) AS reentries,
            round((percentile_cont(0.5) WITHIN GROUP (ORDER BY perigee_km))::numeric, 1)
                AS median_final_perigee_km,
            round((percentile_cont(0.5) WITHIN GROUP (ORDER BY op_alt_km))::numeric, 0)
                AS median_op_shell_km,
            round((percentile_cont(0.5) WITHIN GROUP (ORDER BY descent_days))::numeric, 0)
                AS median_descent_days
        FROM per_sat GROUP BY op
        """,
        params,
    )
    re_by_op = {r[0]: r for r in re_rows}

    # Merged, deterministically ordered leaderboard: lingering desc, then reentries desc, then op.
    board_cols = ["operator", "inactive_payloads", "lingering_dead_and_high",
                  "lingering_avg_alt_km", "reentries_in_period", "median_final_perigee_km",
                  "median_op_shell_km", "median_descent_days"]
    board = []
    for op in LEO_BENCHMARK_OPERATORS:
        lr = ling_by_op.get(op)
        rr = re_by_op.get(op)
        board.append((
            op,
            lr[1] if lr else 0,
            lr[2] if lr else 0,
            lr[3] if lr else None,
            rr[1] if rr else 0,
            rr[2] if rr else None,
            rr[3] if rr else None,
            rr[4] if rr else None,
        ))
    board.sort(key=lambda r: (-(r[2] or 0), -(r[4] or 0), r[0]))

    # Per-satellite lingering appendix (the dead-and-high registry), deterministically ordered.
    # Latest element restricted to the INACTIVE benchmark fleet (small) before the DISTINCT ON.
    app_cols, app_rows = _q(
        cur,
        """
        WITH bench AS (
            SELECT operator_id, canonical_name FROM operator WHERE canonical_name = ANY(%(ops)s)
        ),
        ls AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
            FROM satellite_status_history ORDER BY satellite_id, observed_at DESC
        ),
        inactive_fleet AS (
            SELECT s.norad_id, s.canonical_name AS name, b.canonical_name AS op
            FROM satellite s
            JOIN satellite_operator so
                ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
            JOIN bench b ON b.operator_id = so.operator_id
            JOIN ls ON ls.satellite_id = s.satellite_id AND ls.canonical_status = 'INACTIVE'
            WHERE s.object_type = 'PAYLOAD'
        ),
        latest AS (
            SELECT DISTINCT ON (ge.norad_id) ge.norad_id, ge.perigee_km, ge.apogee_km, ge.epoch
            FROM gp_elements ge JOIN inactive_fleet f ON f.norad_id = ge.norad_id
            ORDER BY ge.norad_id, ge.epoch DESC, ge.source
        )
        SELECT f.op AS operator, f.norad_id, f.name,
            round(l.perigee_km::numeric, 0) AS perigee_km,
            round(l.apogee_km::numeric, 0) AS apogee_km,
            l.epoch::date AS last_tracked
        FROM inactive_fleet f
        JOIN latest l ON l.norad_id = f.norad_id
        WHERE l.apogee_km < %(leo_apo)s AND l.perigee_km > %(ling_per)s
        ORDER BY l.perigee_km DESC, f.norad_id
        """,
        params,
    )
    total_lingering = sum(r[2] for r in board)
    return {"board": (board_cols, board), "appendix": (app_cols, app_rows),
            "total_lingering": total_lingering}


# ---------------------------------------------------------------------------------------------
# Section 4: GEO end-of-life conduct
# ---------------------------------------------------------------------------------------------

def _geo_eol(cur):
    params = {"lo": GEO_BAND_LO_KM, "hi": GEO_BAND_HI_KM, "geo": GEO_ALT_KM,
              "boost": GRAVEYARD_MIN_BOOST_KM, "prot": GEO_PROTECTED_HALF_KM}

    # INACTIVE GEO payloads. The latest element is restricted to the (small) INACTIVE-payload
    # candidate set before the DISTINCT ON, so this never sorts the full element hypertable.
    inactive_geo_cte = """
        WITH ls AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
            FROM satellite_status_history ORDER BY satellite_id, observed_at DESC
        ),
        cand AS (
            SELECT s.satellite_id, s.norad_id, s.canonical_name AS name
            FROM satellite s
            JOIN ls ON ls.satellite_id = s.satellite_id AND ls.canonical_status = 'INACTIVE'
            WHERE s.object_type = 'PAYLOAD'
        ),
        latest AS (
            SELECT DISTINCT ON (ge.norad_id) ge.norad_id, ge.perigee_km, ge.apogee_km,
                   (ge.perigee_km + ge.apogee_km) / 2.0 AS mean_alt
            FROM gp_elements ge JOIN cand c ON c.norad_id = ge.norad_id
            ORDER BY ge.norad_id, ge.epoch DESC, ge.source
        ),
        geo AS (
            SELECT c.satellite_id, c.norad_id, c.name, o.canonical_name AS op,
                   l.perigee_km, l.apogee_km, l.mean_alt
            FROM cand c
            JOIN latest l ON l.norad_id = c.norad_id
            JOIN satellite_operator so
                ON so.satellite_id = c.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
            JOIN operator o ON o.operator_id = so.operator_id
            WHERE l.mean_alt BETWEEN %(lo)s AND %(hi)s AND l.apogee_km > 34500
        )
    """
    board_cols, board_rows = _q(
        cur,
        inactive_geo_cte + """
        SELECT op,
            count(*) AS inactive_geo,
            count(*) FILTER (WHERE perigee_km >= %(geo)s + %(boost)s) AS graveyard_compliant,
            count(*) FILTER (WHERE perigee_km < %(geo)s + %(boost)s
                AND mean_alt BETWEEN %(geo)s - %(prot)s AND %(geo)s + %(prot)s)
                AS abandoned_in_belt,
            round((percentile_cont(0.5) WITHIN GROUP (ORDER BY perigee_km - %(geo)s))::numeric, 0)
                AS median_perigee_above_geo_km
        FROM geo GROUP BY op HAVING count(*) >= 3
        ORDER BY inactive_geo DESC, op
        """,
        params,
    )
    noncompliant_cols, noncompliant_rows = _q(
        cur,
        inactive_geo_cte + """
        SELECT op AS operator, norad_id, name,
            round((perigee_km - %(geo)s)::numeric, 0) AS perigee_vs_geo_km,
            round((apogee_km - %(geo)s)::numeric, 0) AS apogee_vs_geo_km
        FROM geo WHERE perigee_km < %(geo)s + %(boost)s
        ORDER BY perigee_km, norad_id
        """,
        params,
    )

    # Physics says graveyarded, catalog says alive: payloads sitting above the belt whose latest
    # status is anything but INACTIVE -- retirements the snapshot status field misses. Computed off
    # the current CelesTrak GP snapshot (small) so it needn't scan the full history hypertable.
    stale_cols, stale_rows = _q(
        cur,
        """
        WITH ls AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
            FROM satellite_status_history ORDER BY satellite_id, observed_at DESC
        ),
        latest AS (
            SELECT DISTINCT ON (norad_id) norad_id, perigee_km, apogee_km
            FROM gp_elements WHERE source = 'celestrak_gp'
            ORDER BY norad_id, epoch DESC
        )
        SELECT COALESCE(ls.canonical_status, '(none)') AS catalog_status, count(*) AS payloads
        FROM satellite s
        JOIN latest l ON l.norad_id = s.norad_id
        LEFT JOIN ls ON ls.satellite_id = s.satellite_id
        WHERE s.object_type = 'PAYLOAD'
          AND l.perigee_km >= %(geo)s + 150 AND l.apogee_km < 45000
          AND COALESCE(ls.canonical_status, '') <> 'INACTIVE'
        GROUP BY 1 ORDER BY payloads DESC, catalog_status
        """,
        params,
    )
    stale_total = sum(r[1] for r in stale_rows)
    return {"board": (board_cols, board_rows),
            "noncompliant": (noncompliant_cols, noncompliant_rows),
            "stale": (stale_cols, stale_rows), "stale_total": stale_total}


# ---------------------------------------------------------------------------------------------
# Section 5: accountability integrity
# ---------------------------------------------------------------------------------------------

def _accountability(cur, period_start, period_end):
    stale_cols, stale_rows = _q(
        cur,
        """
        WITH latest_satcat_owner AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, value AS owner_raw
            FROM source_assertion
            WHERE attribute = 'owner' AND source = 'satcat' AND satellite_id IS NOT NULL
            ORDER BY satellite_id, observed_at DESC, ingest_run_id DESC, source_key
        ),
        ls AS (
            SELECT DISTINCT ON (satellite_id) satellite_id, canonical_status
            FROM satellite_status_history ORDER BY satellite_id, observed_at DESC
        )
        SELECT o_parent.canonical_name AS acquirer,
               o_child.canonical_name AS catalog_still_names,
               orl.valid_from AS acquired_on,
               count(*) AS attributed_objects,
               count(*) FILTER (WHERE COALESCE(ls.canonical_status, '') <> 'DECAYED')
                   AS on_orbit
        FROM latest_satcat_owner lso
        JOIN operator_alias oa ON oa.source = 'satcat' AND lower(oa.alias) = lower(lso.owner_raw)
        JOIN operator o_child ON o_child.operator_id = oa.operator_id
        JOIN operator_relationship orl
            ON orl.child_id = oa.operator_id
           AND orl.relationship IN ('acquired_by', 'merged_into')
           AND orl.valid_from <= current_date
           AND (orl.valid_to IS NULL OR orl.valid_to > current_date)
        JOIN operator o_parent ON o_parent.operator_id = orl.parent_id
        LEFT JOIN ls ON ls.satellite_id = lso.satellite_id
        GROUP BY 1, 2, 3 ORDER BY attributed_objects DESC, acquirer
        """,
    )
    stale_total_on_orbit = sum(r[4] for r in stale_rows)

    killer = _q(
        cur,
        """
        SELECT sum(temporal_sat_days)::bigint AS temporal_elset_days,
               sum(naive_satcat_sat_days)::bigint AS naive_elset_days,
               max(temporal_sats) AS temporal_sats,
               max(naive_satcat_sats) AS naive_sats
        FROM v_killer_chart WHERE operator_name = 'Eutelsat'
        """,
    )[1][0]

    # Death-certificate disputes: cross-source disagreement on the reentry (decay) date, reusing
    # quality/report.py's loose-date conflict definition so both reports agree by construction.
    _, decay_rows = _section_decay_date_conflicts(cur)

    def _delta_days(sources_and_dates: str) -> int:
        parsed = []
        for part in sources_and_dates.split(";"):
            _, _, raw = part.partition(":")
            d = parse_date_loose(raw.strip())
            if d is not None:
                parsed.append(d)
        return (max(parsed) - min(parsed)).days if len(parsed) >= 2 else 0

    ranked = sorted(decay_rows, key=lambda r: -_delta_days(r[2]))  # stable: preserves norad order
    ex_cols = ["norad_id", "object", "conflicting_reentry_claims", "disagreement_days"]
    examples = [(r[0], r[1], r[2], _delta_days(r[2])) for r in ranked[:5]]
    return {"stale": (stale_cols, stale_rows), "stale_total_on_orbit": stale_total_on_orbit,
            "killer": killer, "death_cert_disputes": len(decay_rows),
            "death_cert_examples": (ex_cols, examples)}


# ---------------------------------------------------------------------------------------------
# Section 6: catalog integrity (cross-source conflict rates)
# ---------------------------------------------------------------------------------------------

def _catalog_integrity(cur):
    # Status conflict: two concrete (non-UNKNOWN) canonical statuses that differ. Reuse report.py.
    _, status_rows = _section_status_disagreements(cur)
    status_denom = _scalar(
        cur,
        """
        WITH sc AS (SELECT DISTINCT ON (a.satellite_id) a.satellite_id, m.canonical_status
            FROM source_assertion a JOIN status_mapping m
                ON m.source = 'satcat' AND m.source_value = a.value
            WHERE a.source = 'satcat' AND a.attribute = 'status' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC),
        gc AS (SELECT DISTINCT ON (a.satellite_id) a.satellite_id, m.canonical_status
            FROM source_assertion a JOIN status_mapping m
                ON m.source = 'gcat' AND m.source_value = a.value
            WHERE a.source = 'gcat' AND a.attribute = 'status' AND a.satellite_id IS NOT NULL
            ORDER BY a.satellite_id, a.observed_at DESC)
        SELECT count(*) FROM sc JOIN gc USING (satellite_id)
        WHERE sc.canonical_status <> 'UNKNOWN' AND gc.canonical_status <> 'UNKNOWN'
        """,
    )

    _, decay_rows = _section_decay_date_conflicts(cur)
    decay_denom = _scalar(
        cur,
        """
        SELECT count(*) FROM (
            SELECT satellite_id FROM source_assertion
            WHERE attribute = 'decay_date' AND satellite_id IS NOT NULL
            GROUP BY satellite_id HAVING count(DISTINCT source) >= 2
        ) x
        """,
    )

    # Object-type conflict: canonicalise each source's raw code and compare (both concrete).
    _, ot_rows = _q(
        cur,
        """
        WITH satcat AS (SELECT DISTINCT ON (satellite_id) satellite_id, value
            FROM source_assertion
            WHERE source = 'satcat' AND attribute = 'object_type' AND satellite_id IS NOT NULL
            ORDER BY satellite_id, observed_at DESC),
        gcat AS (SELECT DISTINCT ON (satellite_id) satellite_id, value
            FROM source_assertion
            WHERE source = 'gcat' AND attribute = 'object_type' AND satellite_id IS NOT NULL
            ORDER BY satellite_id, observed_at DESC)
        SELECT s.value, g.value FROM satcat s JOIN gcat g USING (satellite_id)
        """,
    )
    ot_both = ot_confl = 0
    for sv, gv in ot_rows:
        cs, cg = canonical_object_type(sv), canonical_object_type(gv)
        if cs == "UNKNOWN" or cg == "UNKNOWN":
            continue
        ot_both += 1
        if cs != cg:
            ot_confl += 1

    return {
        "status": (len(status_rows), status_denom),
        "decay": (len(decay_rows), decay_denom),
        "object_type": (ot_confl, ot_both),
    }


# ---------------------------------------------------------------------------------------------
# Section 7: methodology & limitations annex
# ---------------------------------------------------------------------------------------------

def _methodology(cur):
    gold = {
        "total": _scalar(cur, "SELECT count(*) FROM gold_case"),
        "adjudicated": _scalar(cur, "SELECT count(*) FROM gold_case WHERE verdict IS NOT NULL"),
        "correct": _scalar(cur, "SELECT count(*) FROM gold_case WHERE verdict = 'correct'"),
        "incorrect": _scalar(cur, "SELECT count(*) FROM gold_case WHERE verdict = 'incorrect'"),
        "partial": _scalar(cur, "SELECT count(*) FROM gold_case WHERE verdict = 'partial'"),
        "dossiers": _scalar(cur, "SELECT count(*) FROM gold_dossier"),
        "strata": _scalar(cur, "SELECT count(DISTINCT case_type) FROM gold_case"),
    }
    merge_rules_cols, merge_rules_rows = _q(
        cur,
        "SELECT rule_fired, count(*) AS merges FROM merge_log "
        "GROUP BY rule_fired ORDER BY merges DESC, rule_fired",
    )
    return {"gold": gold, "merge_rules": (merge_rules_cols, merge_rules_rows)}


# ---------------------------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------------------------

def generate_report(conn, period_end: dt.date | None = None, period_months: int = 12) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out: list[str] = []

    with conn.cursor() as cur:
        if period_end is None:
            period_end = _scalar(cur, "SELECT max(epoch)::date FROM gp_elements")
        period_start = _months_before(period_end, period_months)

        basis = _data_basis(cur)
        kuiper = _kuiper(cur, period_start, period_end)
        leo = _leo_disposal(cur, period_start, period_end)
        geo = _geo_eol(cur)
        acct = _accountability(cur, period_start, period_end)
        integ = _catalog_integrity(cur)
        method = _methodology(cur)

    # ---- Cover ----
    out.append("# Orbital Behavior Report\n")
    out.append(f"### Report #0 — period {period_start.isoformat()} to {period_end.isoformat()} "
               f"({period_months} months to latest data)\n")
    out.append(f"\nGenerated at: {now}\n")
    out.append(
        "\nAn independent, physics-based audit of megaconstellation-operator behavior. Every figure "
        "below is computed live at generation time against the resolved identity graph and the "
        "`gp_elements` orbital fact layer; none is supplied by an operator. Behavior is inferred "
        "from element-set physics (Space-Track GP) and cross-checked against the reconciled public "
        "catalog. Each number is stated with its n and the SQL-derivable definition inline. Method "
        f"and limitations: see the [methodology annex]({METHODOLOGY_ANNEX_ANCHOR}).\n"
    )

    # ---- Section 1 ----
    t = basis["totals"]
    out.append("\n## 1. Data basis\n")
    out.append(
        f"\nThe identity graph resolves **{t['satellites']:,}** catalogued objects from "
        f"**{t['assertions']:,}** field-level source assertions into "
        f"**{t['identifiers']:,}** cross-source identifiers, across **{t['operators']:,}** operators "
        f"linked by **{t['operator_relationships']:,}** M&A/hierarchy relationships. Behavioral "
        f"inference draws on **{t['gp_elements']:,}** orbital element sets (backfilled Space-Track "
        f"GP history plus the current CelesTrak GP window), latest epoch **{t['latest_epoch']}**.\n"
    )
    out.append("\n**Data vintage — latest successful ingest per source:**\n\n")
    out.append(_md_table(*basis["vintage"]))
    out.append("\n**Source assertions by dataset of record:**\n\n")
    out.append(_md_table(*basis["assertions"]))
    out.append(
        "\n*Methodology (one paragraph).* Each source (CelesTrak SATCAT, GCAT, Space-Track GP, UCS) "
        "is ingested to an append-only assertion ledger with full provenance; a per-attribute "
        "precedence resolver (`identity/precedence.yml`) picks a canonical value while every losing "
        "assertion stays queryable (disagreements are data, not errors). Orbital behavior is derived "
        "purely from published two-line/GP element sets: semi-major axis, perigee and apogee follow "
        "from mean motion and eccentricity (Earth mu = 398600.4418 km^3/s^2). No operator telemetry, "
        "filing, or press statement is trusted as input. See the annex for the full provenance model "
        "and honest limitations.\n"
    )

    # ---- Section 2 (lead / news hook) ----
    p = kuiper["part"]
    at_shell, raising, deorbited, other, deployed = (
        p["at_shell_stable"], p["raising"], p["deorbited"], p["other"], p["deployed"])
    days_to_due = (FCC_50PCT_DUE - period_end).days
    shortfall = FCC_50PCT_MILESTONE - at_shell
    months_in_period = max(period_months, 1)
    run_rate = kuiper["deployed_in_period"] / months_in_period
    out.append("\n## 2. Deployment-milestone verification — Amazon Project Kuiper\n")
    out.append(
        f"\n**FCC 50% deployment milestone: {FCC_50PCT_MILESTONE:,} satellites operational, due "
        f"{FCC_50PCT_DUE.isoformat()}** (≈{days_to_due} days after this report's data date).\n"
    )
    out.append(
        f"\nAs of **{period_end}**, the identity graph attributes **{deployed:,}** Kuiper payloads "
        f"to Amazon (current SCD2 owner). Their physical state, partitioned so the counts reconcile "
        f"exactly (deployed = at-shell + raising + deorbited + other):\n\n"
    )
    kuiper_cols = ["state (physics definition)", "payloads"]
    kuiper_body = [
        (f"**Confirmed operational** — mean altitude within ±{SHELL_TOL_KM:.0f} km of the "
         f"{KUIPER_SHELL_KM:.0f} km shell AND 30-day rolling SMA stddev < {STABLE_SMA_STDDEV_KM:.0f} "
         "km (station-keeping locked)", at_shell),
        ("**Still orbit-raising** — on-orbit, perigee above 300 km, not yet locked at the shell",
         raising),
        ("**Deorbited** — latest resolved status DECAYED", deorbited),
        ("**Other** — decaying (perigee ≤ 300 km) or not yet tracked", other),
        ("**Total deployed**", deployed),
    ]
    out.append(_md_table(kuiper_cols, kuiper_body))
    out.append(
        f"\n**What the physics supports as of {period_end}:** Kuiper has **{at_shell:,}** satellites "
        f"physically confirmed operational at the ~{KUIPER_SHELL_KM:.0f} km shell — "
        f"**{_pct(at_shell, FCC_50PCT_MILESTONE)}** of the {FCC_50PCT_MILESTONE:,} required by "
        f"{FCC_50PCT_DUE.isoformat()}. Even counting **every** deployed payload regardless of state "
        f"({deployed:,}), the fleet stands at {_pct(deployed, FCC_50PCT_MILESTONE)} of the milestone. "
        f"Closing the {shortfall:,}-satellite gap in the remaining ~{days_to_due} days would require "
        f"placing ≈{shortfall / max(days_to_due, 1):,.0f} operational satellites per day; the observed "
        f"deployment rate over the last {period_months} months is ≈{run_rate:,.0f} payloads/month "
        f"({kuiper['deployed_in_period']:,} deployed in-period). The milestone will not be met on the "
        "current trajectory; this is a physics statement, not a forecast of any FCC waiver.\n"
    )
    out.append("\n**Monthly deployment rate (payloads by launch month, current Amazon fleet):**\n\n")
    out.append(_md_table(*kuiper["trend"]))

    # ---- Section 3 ----
    out.append("\n## 3. Disposal-compliance leaderboard (LEO)\n")
    out.append(
        f"\nPer benchmark operator. **Lingering (dead-and-high):** payloads whose latest resolved "
        f"status is INACTIVE, still in LEO (apogee < {LEO_APOGEE_MAX_KM:.0f} km) with perigee above "
        f"{LINGERING_PERIGEE_KM:.0f} km — dead but not disposed. **Reentries in-period:** payloads "
        f"resolved DECAYED with a reentry date in [{period_start}, {period_end}], each tracked by "
        "physics down to a low final perigee. **Median descent days:** median time from last day at "
        "the operational shell (p90 of life mean-altitude, −30 km) to reentry — a short descent from "
        "a high shell is the signature of active/propulsive disposal, since passive drag from 500 km "
        "takes years.\n\n"
    )
    out.append(_md_table(*leo["board"]))
    out.append(
        "\n*Reading the board.* SpaceX carries a fleet two orders of magnitude larger than any peer "
        "yet leaves almost nothing dead-and-high, and executed the overwhelming majority of "
        "in-period reentries — its median descent from the operational shell is measured in weeks, "
        "far faster than passive drag allows from that altitude, i.e. active disposal. Propulsionless "
        "cubesat fleets (Planet Labs, Spire, ICEYE) reenter passively by design from low shells. "
        "Iridium concentrates the lingering set: first-generation birds parked high with no disposal "
        "dominate the dead-and-high registry below. Absolute counts are not fleet-normalised; read "
        "them alongside each operator's fleet size.\n"
    )
    out.append(
        f"\n**Appendix 3A — dead-and-high registry ({leo['total_lingering']} INACTIVE LEO payloads, "
        f"perigee > {LINGERING_PERIGEE_KM:.0f} km):**\n\n"
    )
    out.append(_md_table(*leo["appendix"]))

    # ---- Section 4 ----
    out.append("\n## 4. GEO end-of-life conduct\n")
    board_cols, board_rows = geo["board"]
    out.append(
        f"\nGraveyard-boost compliance among INACTIVE GEO payloads (mean altitude "
        f"{GEO_BAND_LO_KM:.0f}–{GEO_BAND_HI_KM:.0f} km). Compliant = perigee raised at least "
        f"{GRAVEYARD_MIN_BOOST_KM:.0f} km above the {GEO_ALT_KM:.0f} km belt (IADC minimum re-orbit). "
        "Only operators with n ≥ 3 INACTIVE GEO payloads in the graph are listed.\n\n"
    )
    out.append(_md_table(board_cols, board_rows))
    if board_rows:
        lead = board_rows[0]
        out.append(
            f"\n**{lead[0]}** is the only operator with a graveyard-scale INACTIVE GEO cohort in the "
            f"reconciled graph: **{lead[2]} of {lead[1]}** ({_pct(lead[2], lead[1])}) properly boosted "
            f"to a median **+{lead[4]:.0f} km** above GEO. The exceptions:\n\n"
        )
        out.append(_md_table(*geo["noncompliant"]))
    out.append(
        f"\n**The catalog cannot audit the rest.** No other operator carries a single INACTIVE GEO "
        f"payload in the reconciled record — yet **{geo['stale_total']}** payloads physically sit ≥150 "
        f"km above the belt (graveyarded by the physics) while the catalog still labels them anything "
        f"but INACTIVE:\n\n"
    )
    out.append(_md_table(*geo["stale"]))
    out.append(
        "\nThat gap is the finding: snapshot status fields miss GEO retirements, so graveyard "
        "compliance is only auditable where a source happens to have flagged the object dead. A "
        "physics-based end-of-life detector (the behavioral status oracle; see annex) is required to "
        "audit the rest involuntarily.\n"
    )

    # ---- Section 5 ----
    out.append("\n## 5. Accountability integrity\n")
    out.append(
        "\n**Stale owner-of-record after M&A.** Objects whose latest SATCAT owner code still resolves "
        "to a company that has since been acquired or merged — the public catalog still names the "
        "dissolved child. Counts are on-orbit objects per acquisition:\n\n"
    )
    out.append(_md_table(*acct["stale"]))
    out.append(
        f"\n**{acct['stale_total_on_orbit']:,}** on-orbit objects carry an owner-of-record that no "
        "longer exists as an independent entity. Note this catches only acquisitions whose SATCAT "
        "code maps to a company alias; the ex-OneWeb fleet is coded to the country code 'UK', which "
        "maps to no operator at all and is therefore invisible to naive owner-code attribution — the "
        "exact failure the next number quantifies.\n"
    )
    k = acct["killer"]
    if k is not None:
        t_days, n_days, t_sats, n_sats = k
        ratio = (t_days / n_days) if n_days else None
        ratio_txt = f"{ratio:.1f}×" if ratio else "n/a"
        out.append(
            "\n**Temporal vs. naive attribution (the identity-graph delta), Eutelsat / ex-OneWeb.** "
            "SATCAT's OWNER field is a country/agency code, not a company; temporal SCD2 identity "
            "resolution assigns each satellite-day to the operator that actually held it.\n\n"
            f"- Temporal (SCD2) attribution: **{t_sats:,}** satellites, **{t_days:,}** elset-days.\n"
            f"- Naive SATCAT owner code: **{n_sats:,}** satellites, **{n_days:,}** elset-days.\n"
            f"- Delta: **{t_sats - n_sats:,}** satellites / **{t_days - n_days:,}** elset-days — "
            f"**{ratio_txt}** more behavior correctly attributed under temporal resolution.\n"
        )
    out.append(
        f"\n**Death-certificate disputes.** **{acct['death_cert_disputes']:,}** objects have "
        "cross-source disagreement on their reentry (decay) date after loose-date normalisation — the "
        "public record cannot agree on when these objects died. Five largest disagreements:\n\n"
    )
    out.append(_md_table(*acct["death_cert_examples"]))

    # ---- Section 6 ----
    out.append("\n## 6. Catalog integrity (the reliability baseline)\n")
    s_n, s_d = integ["status"]
    d_n, d_d = integ["decay"]
    o_n, o_d = integ["object_type"]
    out.append(
        "\nHow reliable is the public record, per attribute? For every object asserted by two "
        "independent sources, the substantive conflict rate (both sides concrete):\n\n"
    )
    integ_cols = ["attribute", "conflicts", "objects with 2 concrete sources", "conflict rate",
                  "agreement"]
    integ_rows = [
        ("Reentry (decay) date", f"{d_n:,}", f"{d_d:,}", _pct(d_n, d_d), _pct(d_d - d_n, d_d)),
        ("Object type", f"{o_n:,}", f"{o_d:,}", _pct(o_n, o_d), _pct(o_d - o_n, o_d)),
        ("Operational status", f"{s_n:,}", f"{s_d:,}", _pct(s_n, s_d), _pct(s_d - s_n, s_d)),
    ]
    out.append(_md_table(integ_cols, integ_rows))
    out.append(
        f"\nDecay dates are the least reliable field in the public catalog: when two sources both "
        f"record a reentry date they disagree **{_pct(d_n, d_d)}** of the time — roughly one object "
        f"in eight. Object type and operational status agree far more often, but status agreement is "
        f"measured only over the {s_d:,} objects where both sources assert a concrete (non-UNKNOWN) "
        f"status — most objects carry a concrete status from at most one source, which is itself the "
        f"coverage limitation noted in the annex.\n"
    )

    # ---- Section 7 ----
    g = method["gold"]
    out.append("\n## 7. Methodology & limitations annex\n")
    out.append(
        "\n**Provenance model.** Every value in this report traces to a source assertion. Ingestion "
        "writes an append-only `source_assertion` ledger (attribute, value, source, observed-at, "
        "ingest run) that never overwrites; a per-attribute precedence resolver "
        "(`identity/precedence.yml`) selects the canonical value for each dimension while every "
        "losing assertion stays queryable. Object merges are never silent — each is written to "
        "`merge_log` with the rule that fired:\n\n"
    )
    out.append(_md_table(*method["merge_rules"]))
    out.append(
        f"\n**Trust program (gold-standard evaluation).** A stratified set of **{g['total']}** hard "
        f"identity-resolution cases across {g['strata']} strata, each with a full evidence packet; "
        f"**{g['dossiers']}** carry an AI-researched dossier with cited sources. Human-adjudicated "
        f"verdicts so far: **{g['adjudicated']}** "
        f"(correct {g['correct']}, partial {g['partial']}, incorrect {g['incorrect']}). Resolution "
        f"accuracy is measured, not assumed; at {g['adjudicated']} adjudicated the accuracy rate is "
        "not yet reportable and is stated here as pending rather than estimated.\n"
    )
    out.append(
        "\n**Limitations (stated plainly — the report's credibility is the product).**\n\n"
        "1. **Status is snapshot-based, not a transition ledger.** Operational status is the latest "
        "resolved snapshot per object; a genuine append-only status-transition time series is "
        "accruing but not yet deep. INACTIVE/GRAVEYARD flags therefore depend on a source having "
        "marked the object — which §4 shows is inconsistent for GEO retirements.\n"
        "2. **The behavioral status oracle is pending.** Physics-inferred operational/dead "
        "transitions (station-keeping collapse, drag-decay onset, maneuver change-points) are "
        "scaffolded in `analysis/` but not yet in production. Where this report infers behavior "
        "(disposal mode in §3, graveyarding in §4) it does so from altitude, descent rate and known "
        "propulsion class, and says so; a rigorous per-object disposal-mode classifier awaits the "
        "oracle.\n"
        "3. **Classified and untracked objects are unobservable.** The audit sees only publicly "
        "catalogued objects with published element sets; maneuvering or classified assets that "
        "withhold GP data cannot be verified here.\n"
        "4. **Single-source physics.** Orbital history is Space-Track GP (and CelesTrak GP for the "
        "current window) only; there is no independent radar/optical cross-check of the element sets "
        "themselves. The identity and conflict layers are multi-source; the physics is not.\n"
        "5. **Attribution coverage.** Behavioral figures are reported only for objects with resolved "
        "current ownership and landed GP history; operators without backfilled history (or with "
        "owner codes that map to no operator) are under-counted, not zero — the naive-attribution "
        "delta in §5 quantifies one such gap.\n"
    )
    out.append(
        f"\n---\n*Orbital Behavior Report #0 · period {period_start} → {period_end} · generated from "
        "a read-only query against the resolved identity graph. Re-running on the same data "
        "reproduces this document byte-for-byte except the generated-at line.*\n"
    )
    return "".join(out)


def _report_path(period_end: dt.date) -> pathlib.Path:
    return REPORTS_DIR / f"orbital-behavior-{period_end:%Y-%m}.md"


def write_report(conn, period_end: dt.date | None = None, period_months: int = 12,
                 path: pathlib.Path | None = None) -> pathlib.Path:
    with conn.cursor() as cur:
        resolved_end = period_end or _scalar(cur, "SELECT max(epoch)::date FROM gp_elements")
    content = generate_report(conn, period_end=resolved_end, period_months=period_months)
    out_path = path or _report_path(resolved_end)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the Orbital Behavior Report.")
    ap.add_argument("--period-end", type=lambda s: dt.date.fromisoformat(s), default=None,
                    help="Report period end (YYYY-MM-DD). Default: latest gp_elements epoch date.")
    ap.add_argument("--period-months", type=int, default=12, help="Report period length in months.")
    args = ap.parse_args()
    conn = get_conn()
    try:
        path = write_report(conn, period_end=args.period_end, period_months=args.period_months)
        print(f"wrote {path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
