"""Contract + internal-consistency tests for GET /api/audit/summary (the Overview audit strip).

DB-marked: a FastAPI TestClient runs against the live dev DB (read-only), computing the three
auditor headline numbers live. Assertions are on shape + internal consistency, not exact counts
(which move with each ingest), so the tests stay honest as the data grows.
"""

import warnings

import pytest
from psycopg.rows import dict_row

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app
from api.routers import audit


@pytest.fixture
def client(db_conn):  # db_conn: skips the whole module when the dev DB is unreachable
    return TestClient(app)


@pytest.mark.db
def test_audit_summary_shape(client):
    r = client.get("/api/audit/summary")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"kuiper_milestone", "lingering_leaderboard", "active_but_decaying"}


@pytest.mark.db
def test_kuiper_milestone_internally_consistent(client):
    km = client.get("/api/audit/summary").json()["kuiper_milestone"]
    assert {"at_shell", "raising", "deorbited", "deployed_total", "deployed_last_30d",
            "deployed_by_deadline", "required", "deadline"} <= set(km)
    # DA 26-553 para. 3: 1,616 by 2026-07-30, half of the 3,232 left after the 2024 modification.
    assert km["required"] == 1616
    assert km["deadline"] == "2026-07-30"
    for key in ("at_shell", "raising", "deorbited", "deployed_total", "deployed_last_30d"):
        assert isinstance(km[key], int) and km[key] >= 0
    # The three orbit buckets partition a subset of the fleet -- never more than were deployed.
    assert km["at_shell"] + km["raising"] + km["deorbited"] <= km["deployed_total"]
    # What was in orbit on the deadline is a fixed subset of what has been launched at all.
    assert 0 <= km["deployed_by_deadline"] <= km["deployed_total"]
    # The real build is nowhere near the 1,616 obligation -- that gap IS the thesis.
    assert km["deployed_total"] < km["required"]


@pytest.mark.db
@pytest.mark.graph
def test_lingering_leaderboard_nonempty_and_shaped(client):
    rows = client.get("/api/audit/summary").json()["lingering_leaderboard"]
    assert rows, "the lingering leaderboard should have at least one benchmark operator"
    assert len(rows) <= 6
    prev = None
    for row in rows:
        assert {"operator", "count", "avg_alt_km"} <= set(row)
        assert isinstance(row["operator"], str) and row["operator"]
        assert isinstance(row["count"], int) and row["count"] > 0
        assert isinstance(row["avg_alt_km"], (int, float)) and row["avg_alt_km"] > 500
        if prev is not None:
            assert row["count"] <= prev, "leaderboard must be ordered by count descending"
        prev = row["count"]


@pytest.mark.db
@pytest.mark.graph
def test_active_but_decaying_positive(client):
    n = client.get("/api/audit/summary").json()["active_but_decaying"]
    assert isinstance(n, int)
    assert n > 0, "the catalog-active / physics-decaying count is the headline number"


@pytest.mark.db
def test_deadline_count_is_what_was_in_orbit_on_the_deadline(db_conn):
    """deployed_by_deadline counts the fleet that was in orbit on the FCC date: launched on or
    before it and not reentered by it. A satellite that came down before the deadline (the two
    2023 Kuiper protoflights, and four production satellites in 2026) cannot count toward a
    milestone that is judged on satellites launched and operational that day (DA 26-553 para. 12),
    and a launch after the deadline counts toward progress but never toward the verdict.

    Planted under a reserved operator name in an uncommitted transaction, so it runs on an empty
    database and never touches the real Amazon fleet."""
    op = "zz_test_kuiper_op"
    rows = [
        # (norad_id, launch_date, decay_date)
        (940000101, "2023-10-06", "2024-09-29"),  # came down long before the deadline
        (940000102, "2025-06-23", "2026-07-28"),  # came down two days before it
        (940000103, "2025-06-23", None),  # still up: counts
        (940000104, "2026-07-30", None),  # launched on the day: counts
        (940000105, "2025-09-25", "2026-08-15"),  # came down after it: counts
        (940000106, "2026-08-01", None),  # launched after it: progress only
    ]
    with db_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO operator (canonical_name) VALUES (%s) RETURNING operator_id", (op,)
        )
        op_id = cur.fetchone()["operator_id"]
        for norad, launched, decayed in rows:
            cur.execute(
                "INSERT INTO satellite (norad_id, canonical_name, object_type, launch_date, "
                "decay_date) VALUES (%s, %s, 'PAYLOAD', %s, %s) RETURNING satellite_id",
                (norad, f"ZZ TEST {norad}", launched, decayed),
            )
            sid = cur.fetchone()["satellite_id"]
            cur.execute(
                "INSERT INTO satellite_operator (satellite_id, operator_id, role, valid_from, "
                "source) VALUES (%s, %s, 'owner', %s, 'test')",
                (sid, op_id, launched),
            )
        cur.execute(
            audit._KUIPER_SQL,
            {"op": op, "lo": audit._KUIPER_SHELL_LO, "hi": audit._KUIPER_SHELL_HI,
             "deadline": audit.KUIPER_DEADLINE},
        )
        k = cur.fetchone()
    db_conn.rollback()
    assert k["deployed_total"] == 6
    assert k["deployed_by_deadline"] == 3
