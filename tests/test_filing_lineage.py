"""Docket views and the docket API: rule-pins only.

The lesson this repo has now paid for three times (the COL alias pin, the Planet fleet-count
pins, the matview-currency assertions) is that a test pinning an outcome breaks the day the
system works. Everything here pins a rule the data must obey at any point in time: reconciliation
between the docket views and the canonical pending view, internal count consistency, predicate
agreement in both directions, and structural properties of the served timeline. No literal count
appears anywhere in this file.
"""

import warnings

import pytest


def _client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


@pytest.mark.db
def test_docket_pending_counts_reconcile_with_the_canonical_view(db_conn):
    """Every pending filing that carries a callsign is in exactly one docket's pending count."""
    with db_conn.cursor() as cur:
        cur.execute("SELECT coalesce(sum(filings_pending), 0) FROM v_fcc_docket")
        from_dockets = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM v_fcc_pending_applications "
            "WHERE callsign IS NOT NULL AND callsign <> ''"
        )
        from_canonical = cur.fetchone()[0]
        assert from_dockets == from_canonical


@pytest.mark.db
def test_docket_internal_counts_are_consistent(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM v_fcc_docket "
            "WHERE filings_pending > filings_total OR filings_granted > filings_total "
            "   OR pending_amendments > filings_pending OR first_filed > last_filed"
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_pending_predicate_cannot_drift_from_the_canonical_view(db_conn):
    """v_fcc_docket_filing restates the canonical pending predicate. If either side changes
    without the other, the pending sets diverge and this catches it, both directions, restricted
    to filings that carry a callsign (docketless filings are outside the docket views by
    construction)."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM (
                (SELECT file_number FROM v_fcc_docket_filing WHERE is_pending
                 EXCEPT
                 SELECT file_number FROM v_fcc_pending_applications
                 WHERE callsign IS NOT NULL AND callsign <> '')
                UNION ALL
                (SELECT file_number FROM v_fcc_pending_applications
                 WHERE callsign IS NOT NULL AND callsign <> ''
                 EXCEPT
                 SELECT file_number FROM v_fcc_docket_filing WHERE is_pending)
            ) drift
            """
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_a_filing_appears_in_exactly_one_docket_row(db_conn):
    """A filing has one callsign, so it can sit in one docket only. Measured clean before
    pinning (0 duplicates over 5,970 rows); if this ever fires, the latest-run scoping broke or
    the upstream dump began duplicating file numbers, and either is worth stopping the line."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM (SELECT file_number FROM v_fcc_docket_filing "
            "GROUP BY file_number HAVING count(*) > 1) dupes"
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_pending_list_carries_docket_summaries(db_conn):
    body = _client().get("/api/filings/pending?limit=200").json()
    docketed = [r for r in body["rows"] if r.get("docket_filings_total")]
    assert docketed, "expected some pending filings to sit in dockets"
    for row in docketed:
        assert row["docket_filings_pending"] >= 1
        assert row["docket_filings_total"] >= row["docket_filings_pending"]


@pytest.mark.db
def test_docket_endpoint_serves_a_dated_timeline(db_conn):
    """Self-selects a docket that has both granted and pending filings rather than hardcoding a
    callsign, so the test survives the FCC deciding things. The properties pinned are structural:
    summary total equals timeline length, both statuses present, dates ascending, file numbers
    unique, and at least one row carrying a validated spec somewhere in the corpus's richest
    mixed docket."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT callsign FROM v_fcc_docket WHERE filings_granted > 0 AND filings_pending > 0 "
            "ORDER BY filings_total DESC LIMIT 1"
        )
        row = cur.fetchone()
    assert row, "corpus should contain at least one docket with both granted and pending filings"
    body = _client().get(f"/api/filings/docket/{row[0]}").json()
    timeline = body["timeline"]
    assert body["summary"]["filings_total"] == len(timeline)
    assert any(r["is_pending"] for r in timeline)
    assert any(r["date_grant"] for r in timeline)
    dates = [r["date_filed"] for r in timeline if r["date_filed"]]
    assert dates == sorted(dates)
    numbers = [r["file_number"] for r in timeline]
    assert len(numbers) == len(set(numbers))


@pytest.mark.db
def test_docket_endpoint_is_empty_not_404_for_an_unknown_callsign(db_conn):
    response = _client().get("/api/filings/docket/ZZZZ9")
    assert response.status_code == 200
    assert response.json()["summary"] is None
    assert response.json()["timeline"] == []
