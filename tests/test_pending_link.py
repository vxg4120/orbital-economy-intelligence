"""Pending-applications forward signal (methodology v1.7, section 5.8).

The join is a curated FRN allowlist, so what the tests defend is the honesty of the boundary:
applicant identity must be fully landed (no silent NULL matching), only curated cohorts light
up, counts reconcile against their receipt endpoint, and the signal never leaks onto the
leaderboard or into frozen snapshots. Invariant-form assertions: live counts against live
counts, no literals that break when the FCC decides an application.
"""

import warnings
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
APPLICANTS_YML = REPO_ROOT / "identity" / "fcc_applicants.yml"


def _client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


@pytest.mark.db
def test_pending_view_carries_applicant_identity(db_conn):
    """Every pending application resolves an applicant name and FRN. A drop below full
    coverage means the address join regressed (wrong key column, missed run pairing), which
    would silently shrink every cohort's pending count."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(applicant_name), count(applicant_frn) "
            "FROM v_fcc_pending_applications"
        )
        total, with_name, with_frn = cur.fetchone()
    assert total > 300, "pending queue collapsed; check the IBFS landing"
    assert with_name == total, f"{total - with_name} pending applications lost applicant_name"
    assert with_frn == total, f"{total - with_frn} pending applications lost applicant_frn"


@pytest.mark.db
def test_link_table_matches_the_yml_and_only_live_cohorts(db_conn):
    """fcc_applicant_link is exactly the yml (FRN set equality) and every slug it stores is a
    live leaderboard cohort. The build-time alias resolution means retired slugs may be
    REWRITTEN, never dropped, so the FRN sets must still agree."""
    spec = yaml.safe_load(APPLICANTS_YML.read_text(encoding="utf-8"))
    yml_frns = {e["frn"] for e in spec["applicants"]}
    with db_conn.cursor() as cur:
        cur.execute("SELECT frn, manufacturer_slug FROM fcc_applicant_link")
        rows = cur.fetchall()
        cur.execute("SELECT manufacturer_slug FROM v_bus_benchmarks_manufacturer")
        live = {r[0] for r in cur.fetchall()}
    assert {r[0] for r in rows} == yml_frns
    dead = {slug for _, slug in rows} - live
    assert not dead, f"link table points at cohorts that do not exist: {sorted(dead)}"
    assert all(frn.isdigit() and len(frn) == 10 for frn, _ in rows), "FRNs are 10-digit strings"


@pytest.mark.db
def test_forward_signal_fires_and_incumbent_operators_stay_dark(db_conn):
    """Anti-vacuity plus the honesty boundary: curated builder cohorts match a real share of
    the queue, while the largest applicants (incumbent GEO operators who buy their
    spacecraft) match no cohort at all."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM v_fcc_pending_applications p "
            "JOIN fcc_applicant_link l ON l.frn = p.applicant_frn"
        )
        matched = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM v_fcc_pending_applications p "
            "WHERE p.applicant_name ILIKE '%intelsat%' AND EXISTS "
            "(SELECT 1 FROM fcc_applicant_link l WHERE l.frn = p.applicant_frn)"
        )
        intelsat_leaks = cur.fetchone()[0]
    assert matched > 50, "forward signal went vacuous; check the FRN link build"
    assert intelsat_leaks == 0, "an operator-only applicant leaked into a builder cohort"


@pytest.mark.db
def test_detail_count_reconciles_with_receipts(db_conn):
    """pending_applications.pending_n == the applicant_slug receipt endpoint's total, on the
    cohort with the deepest pending queue (spx: SpaceX files continuously)."""
    client = _client()
    d = client.get("/api/buses/spx").json()
    pa = d["pending_applications"]
    assert pa is not None and pa["pending_n"] > 0
    assert 1 <= len(pa["sample"]) <= 3
    r = client.get("/api/filings/pending?applicant_slug=spx&limit=200").json()
    assert r["total"] == pa["pending_n"]
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM v_fcc_pending_applications p "
            "JOIN fcc_applicant_link l ON l.frn = p.applicant_frn "
            "WHERE l.manufacturer_slug = 'spx'"
        )
        direct = cur.fetchone()[0]
    assert pa["pending_n"] == direct


@pytest.mark.db
def test_signal_is_detail_only_and_null_where_it_should_be(db_conn):
    """No leaderboard column, no snapshot key, null on bus models and on cohorts with no
    curated filings."""
    client = _client()
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_name IN ('v_bus_benchmarks_manufacturer', 'v_bus_benchmarks_bus', "
            "'v_bus_benchmarks_manufacturer_anchored', 'v_bus_benchmarks_bus_anchored') "
            "AND (column_name LIKE '%pending%' OR column_name LIKE '%applic%')"
        )
        assert cur.fetchall() == []
        cur.execute(
            "SELECT count(*) FROM bus_benchmark_snapshots, "
            "LATERAL jsonb_object_keys(metrics) AS k WHERE k LIKE '%pending%'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT bus_slug FROM v_bus_benchmarks_bus LIMIT 1")
        bus_slug = cur.fetchone()[0]
    assert client.get(f"/api/buses/{bus_slug}?kind=bus").json()["pending_applications"] is None
    # A cohort with a fleet but no FCC filings of its own stays null rather than zero-object.
    assert client.get("/api/buses/terran").json()["pending_applications"] is None


def test_filings_q_searches_applicant_name():
    client = _client()
    r = client.get("/api/filings/pending?q=intuitive").json()
    assert r["total"] >= 1
    assert any("intuitive" in (row["applicant_name"] or "").lower() for row in r["rows"])
