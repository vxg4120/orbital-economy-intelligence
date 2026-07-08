"""SupGP cross-tag anomaly scraper tests: parses the REAL CelesTrak index structure.

The real page (saved verbatim as supgp_index_real.html, verified live 2026-07-07) encodes
per-constellation anomaly counts as <b class=warn|good>N</b> badges next to 'Matching Results'
links; the literal 'NO MATCH' appears only in a legend paragraph. These tests parse against that
real fixture. DB tests use a unique endpoint tag so they never collide with the real 'supgp_index'
freshness ledger row on the shared dev DB, and clean up everything they create.
"""

from pathlib import Path

import pytest
import responses

from ingest import supgp_crosstags
from tests.fixtures.dbutil import cleanup_since, run_id_baseline

FIXTURE_REAL = Path(__file__).parent / "fixtures" / "supgp_index_real.html"
FIXTURE_ALL_GOOD = Path(__file__).parent / "fixtures" / "supgp_index_all_good.html"
FIXTURE_NO_BADGES = Path(__file__).parent / "fixtures" / "supgp_index_no_badges.html"


@pytest.fixture
def clean_db(db_conn):
    baseline = run_id_baseline(db_conn)
    yield db_conn
    cleanup_since(db_conn, baseline)


# --- pure parser tests (no DB) ------------------------------------------------


def test_extract_anomaly_rows_parses_real_warn_badges():
    rows = supgp_crosstags.extract_anomaly_rows(FIXTURE_REAL.read_text())

    by_tag = {r["file_tag"]: r for r in rows}
    # Exactly the constellations reporting warn (>0) badges on the real page.
    assert set(by_tag) == {"starlink", "kuiper", "planet", "ses", "cpf", "ast"}
    assert all(r["flag"] == supgp_crosstags.MATCH_WARNINGS_FLAG for r in rows)
    assert all(r["norad_id"] is None for r in rows)
    assert "1178" in by_tag["starlink"]["detail"]  # 1,178 comma-formatted count parsed
    assert "9" in by_tag["cpf"]["detail"]


def test_parse_match_badges_counts_warn_and_good_but_ignores_legend():
    html = FIXTURE_REAL.read_text()
    badges = supgp_crosstags.parse_match_badges(html)

    assert sum(b["badge_class"] == "warn" for b in badges) == 6
    assert sum(b["badge_class"] == "good" for b in badges) == 7
    # The legend paragraph carries the literal 'NO MATCH' and non-numeric warn/good badges;
    # neither must produce an anomaly row.
    assert "NO MATCH" in html
    assert all(isinstance(b["count"], int) for b in badges)


def test_extract_anomaly_rows_all_good_returns_empty():
    assert supgp_crosstags.extract_anomaly_rows(FIXTURE_ALL_GOOD.read_text()) == []


def test_extract_anomaly_rows_no_badges_returns_empty():
    assert supgp_crosstags.extract_anomaly_rows(FIXTURE_NO_BADGES.read_text()) == []


# --- DB / run() tests ---------------------------------------------------------


@pytest.mark.db
@responses.activate
def test_run_lands_anomaly_rows(clean_db, monkeypatch):
    monkeypatch.setattr(supgp_crosstags, "ENDPOINT", "supgp_index_test_lands")
    responses.add(responses.GET, supgp_crosstags.URL, body=FIXTURE_REAL.read_text(), status=200)

    n = supgp_crosstags.run(clean_db)

    assert n == 6
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT file_tag, flag FROM raw_supgp_status r "
            "JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id "
            "WHERE i.endpoint = 'supgp_index_test_lands' ORDER BY file_tag"
        )
        rows = cur.fetchall()
    assert {r[0] for r in rows} == {"starlink", "kuiper", "planet", "ses", "cpf", "ast"}
    assert all(flag == "MATCH_WARNINGS" for _, flag in rows)

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status, notes FROM ingest_run WHERE endpoint = 'supgp_index_test_lands' "
            "ORDER BY ingest_run_id DESC LIMIT 1"
        )
        status, notes = cur.fetchone()
    assert status == "ok"
    assert "cross-tag" in notes


@pytest.mark.db
@responses.activate
def test_run_all_good_logs_ok_with_no_anomalies(clean_db, monkeypatch):
    monkeypatch.setattr(supgp_crosstags, "ENDPOINT", "supgp_index_test_allgood")
    responses.add(responses.GET, supgp_crosstags.URL, body=FIXTURE_ALL_GOOD.read_text(), status=200)

    n = supgp_crosstags.run(clean_db)

    assert n == 0
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status, notes FROM ingest_run WHERE endpoint = 'supgp_index_test_allgood' "
            "ORDER BY ingest_run_id DESC LIMIT 1"
        )
        status, notes = cur.fetchone()
    assert status == "ok"
    assert "class=good" in notes


@pytest.mark.db
@responses.activate
def test_run_structure_changed_logs_error(clean_db, monkeypatch):
    """Zero Matching-Results badges of any kind -> the page structure changed; the run must be
    recorded 'error', not a silent 'ok' with 0 rows."""
    monkeypatch.setattr(supgp_crosstags, "ENDPOINT", "supgp_index_test_changed")
    responses.add(responses.GET, supgp_crosstags.URL, body=FIXTURE_NO_BADGES.read_text(), status=200)

    n = supgp_crosstags.run(clean_db)

    assert n == 0
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT status, notes FROM ingest_run WHERE endpoint = 'supgp_index_test_changed' "
            "ORDER BY ingest_run_id DESC LIMIT 1"
        )
        status, notes = cur.fetchone()
    assert status == "error"
    assert "structure changed" in notes


@pytest.mark.db
@responses.activate
def test_run_skips_when_fresh(clean_db, monkeypatch):
    """Second run() within the freshness window makes zero new HTTP calls (finding #9)."""
    monkeypatch.setattr(supgp_crosstags, "ENDPOINT", "supgp_index_test_fresh")
    responses.add(responses.GET, supgp_crosstags.URL, body=FIXTURE_REAL.read_text(), status=200)

    first = supgp_crosstags.run(clean_db)
    assert first == 6
    assert len(responses.calls) == 1

    second = supgp_crosstags.run(clean_db)
    assert second == 0
    assert len(responses.calls) == 1  # no new HTTP call on the fresh re-run
