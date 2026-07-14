"""Tests for quality/audit_report.py -- the Orbital Behavior Report generator.

DB-backed and read-only: these run against the live identity graph (no fixture seeding), so they
are marked ``db`` and skip cleanly when DATABASE_URL is unreachable. They are deliberately light --
the report is generated at most twice across the whole module (a shared module-scoped fixture plus
one re-generation for the determinism check).

What they assert:
  * the generator runs end-to-end and the rendered markdown carries all seven section headers;
  * the Kuiper deployment partition is internally consistent (deployed = at-shell + raising +
    deorbited + other) AND reconciles with an independent count of the current Amazon payload fleet;
  * the report is byte-identical across runs except the single generated-at line;
  * cross-source conflict counts never exceed their denominators (rate sanity).
"""

import datetime as dt

import psycopg
import pytest

from common.db import get_conn
from quality import audit_report as ar

SECTION_HEADERS = [
    "## 1. Data basis",
    "## 2. Deployment-milestone verification",
    "## 3. Disposal-compliance leaderboard (LEO)",
    "## 4. GEO end-of-life conduct",
    "## 5. Accountability integrity",
    "## 6. Catalog integrity",
    "## 7. Methodology & limitations annex",
]


@pytest.fixture(scope="module")
def report():
    """(connection, rendered_markdown) generated once against the live DB; skip if unreachable."""
    try:
        conn = get_conn()
    except psycopg.OperationalError:
        pytest.skip("database not reachable at DATABASE_URL")
        return
    conn.execute("SET default_transaction_read_only = on")
    try:
        yield conn, ar.generate_report(conn)
    finally:
        conn.rollback()
        conn.close()


def _strip_generated_at(md: str) -> str:
    return "\n".join(line for line in md.splitlines() if not line.startswith("Generated at:"))


@pytest.mark.db
def test_report_has_all_seven_section_headers_and_cover(report):
    _, md = report
    assert md.startswith("# Orbital Behavior Report")
    for header in SECTION_HEADERS:
        assert header in md, f"missing section header: {header}"
    # The lead section names the FCC milestone it audits against (rendered with a thousands sep).
    assert f"{ar.FCC_50PCT_MILESTONE:,}" in md


@pytest.mark.db
def test_kuiper_partition_reconciles(report):
    conn, _ = report
    with conn.cursor() as cur:
        period_end = ar._scalar(cur, "SELECT max(epoch)::date FROM gp_elements")
        period_start = ar._months_before(period_end, 12)
        kuiper = ar._kuiper(cur, period_start, period_end)
        part = kuiper["part"]
        # Independent count of the current Amazon payload fleet (the "deployed" universe).
        fleet_n = ar._scalar(
            cur,
            """
            SELECT count(*)
            FROM satellite s
            JOIN satellite_operator so
                ON so.satellite_id = s.satellite_id AND so.role = 'owner' AND so.valid_to IS NULL
            JOIN operator o ON o.operator_id = so.operator_id
            WHERE o.canonical_name = %s AND s.object_type = 'PAYLOAD'
            """,
            (ar.KUIPER_OPERATOR,),
        )
    buckets = ("at_shell_stable", "raising", "deorbited", "other")
    assert part["deployed"] == sum(part[b] for b in buckets)
    assert part["deployed"] == fleet_n, "partition total must equal the independent fleet count"
    assert all(part[b] >= 0 for b in buckets)
    # This report is meaningful only while Kuiper is still deploying; guard the premise.
    assert part["deployed"] > 0


@pytest.mark.db
def test_report_is_byte_identical_across_runs_modulo_timestamp(report):
    conn, first = report
    second = ar.generate_report(conn)
    assert _strip_generated_at(first) == _strip_generated_at(second)


@pytest.mark.db
def test_catalog_integrity_conflicts_never_exceed_denominators(report):
    conn, _ = report
    with conn.cursor() as cur:
        integ = ar._catalog_integrity(cur)
    for attribute, (numer, denom) in integ.items():
        assert denom > 0, f"{attribute}: empty denominator"
        assert 0 <= numer <= denom, f"{attribute}: conflicts {numer} outside [0, {denom}]"


@pytest.mark.db
def test_period_derivation_defaults_to_latest_data(report):
    conn, md = report
    with conn.cursor() as cur:
        latest = ar._scalar(cur, "SELECT max(epoch)::date FROM gp_elements")
    expected_start = ar._months_before(latest, 12)
    assert f"period {expected_start.isoformat()} to {latest.isoformat()}" in md


def test_months_before_is_calendar_correct_and_clamps():
    assert ar._months_before(dt.date(2026, 7, 12), 12) == dt.date(2025, 7, 12)
    assert ar._months_before(dt.date(2026, 3, 31), 1) == dt.date(2026, 2, 28)  # clamp to Feb
    assert ar._months_before(dt.date(2026, 1, 15), 1) == dt.date(2025, 12, 15)  # year rollover
