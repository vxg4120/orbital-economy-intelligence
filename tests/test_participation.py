"""Co-builder participation metric (methodology v1.6, Phase 4 brief section 6).

The design's one non-negotiable promise is that the metric is ADDITIVE: fleet_total stays
prime-position-only on every cohort, participated_total lives on detail payloads only, and the
two reconcile against separate receipt sets. The tests here pin that promise structurally
(the leaderboard views and frozen snapshots must not know the metric exists) and pin the
bridge against the headline build (position-1 credits must reproduce the headline slug
exactly, or the credit resolver has drifted from the attribution resolver).

Invariant-form assertions throughout: counts compare against other live counts, never
against literals that break on the next launch.
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
def test_position_one_reproduces_the_headline_exactly(db_conn):
    """Both directions: every headline attribution has a position-1 credit with the SAME slug,
    and no position-1 credit disagrees. This is the drift alarm between the credit resolver
    and the attribution resolver, which share walk/merge code but run as separate statements."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM satellite_bus sb
            JOIN satellite_manufacturer_credit c
              ON c.satellite_id = sb.satellite_id AND c.position = 1
            WHERE sb.manufacturer_slug IS NOT NULL
              AND c.manufacturer_slug <> sb.manufacturer_slug
            """
        )
        assert cur.fetchone()[0] == 0, "a position-1 credit disagrees with the headline slug"
        cur.execute(
            """
            SELECT count(*) FROM satellite_bus sb
            WHERE sb.manufacturer_slug IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM satellite_manufacturer_credit c
                              WHERE c.satellite_id = sb.satellite_id AND c.position = 1
                                AND c.manufacturer_slug = sb.manufacturer_slug)
            """
        )
        assert cur.fetchone()[0] == 0, "a headline attribution is missing its position-1 credit"


@pytest.mark.db
def test_uncertain_nonfirst_positions_are_never_credited(db_conn):
    """A '?' on a non-first token is a GCAT guess and is not promoted into a credit. The prime
    position keeps its row (the headline publishes the flag instead of dropping the row)."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM satellite_manufacturer_credit WHERE position > 1 AND uncertain"
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_joint_builds_actually_exist(db_conn):
    """Anti-vacuity: the catalog carries hundreds of joint builds, so the co-credit rules must
    fire on a real population rather than passing on an empty one."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE position > 1), "
            "       count(DISTINCT satellite_id) FILTER (WHERE arity > 1) "
            "FROM satellite_manufacturer_credit"
        )
        co_credits, joint_sats = cur.fetchone()
    assert co_credits > 500, "co-builder credits collapsed; the expansion went vacuous"
    assert joint_sats > 500, "joint-build satellites collapsed; the expansion went vacuous"


@pytest.mark.db
def test_participated_never_undercounts_the_fleet(db_conn):
    """participated_total >= fleet_total for every cohort (prime credits are a subset), with
    strict inequality somewhere (the metric must add something, or it is dead weight)."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            WITH p AS (SELECT manufacturer_slug AS slug, count(*) AS participated
                       FROM satellite_manufacturer_credit GROUP BY 1)
            SELECT count(*) FILTER (WHERE p.participated < v.fleet_total),
                   count(*) FILTER (WHERE p.participated > v.fleet_total)
            FROM v_bus_benchmarks_manufacturer v
            JOIN p ON p.slug = v.manufacturer_slug
            """
        )
        undercounts, gainers = cur.fetchone()
    assert undercounts == 0
    assert gainers > 50, "no cohort gains from participation; the join went vacuous"


@pytest.mark.db
def test_leaderboard_and_snapshots_do_not_know_the_metric(db_conn):
    """The additive-only promise, structurally: no leaderboard view exposes a participation
    column, and no frozen snapshot metrics blob carries a participation key. If either fails,
    the metric leaked into a published headline surface."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_name IN ('v_bus_benchmarks_manufacturer', 'v_bus_benchmarks_bus',
                                 'v_bus_benchmarks_manufacturer_anchored',
                                 'v_bus_benchmarks_bus_anchored')
              AND column_name LIKE '%particip%'
            """
        )
        assert cur.fetchall() == []
        cur.execute(
            """
            SELECT count(*) FROM bus_benchmark_snapshots,
                 LATERAL jsonb_object_keys(metrics) AS k
            WHERE k LIKE '%particip%'
            """
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_v_bus_sat_stays_one_row_per_satellite(db_conn):
    """The brief's hard constraint: the credit layer is a separate bridge table, never a
    widening of v_bus_sat, because v_bus_benchmarks_bus reads that view and compound
    satellites would silently double-count in every bus-model metric."""
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT satellite_id) FROM v_bus_sat")
        total, distinct = cur.fetchone()
    assert total == distinct


@pytest.mark.db
def test_detail_payload_reconciles_with_receipts(db_conn):
    """participated_total on the detail payload == role=participated receipt total, and the
    default role=prime receipts still reconcile to fleet_total, on a cohort with real
    co-credits (terran: GCAT lists Terran Orbital second on dozens of joint builds)."""
    client = _client()
    d = client.get("/api/buses/terran").json()
    part = d["participation"]
    assert part is not None
    assert part["participated_total"] >= d["benchmark"]["fleet_total"]
    assert part["co_builder_credits"] > 0, "terran lost its co-credits; check the bridge build"

    r_part = client.get(
        "/api/buses/terran/provenance?metric=fleet&role=participated&limit=500").json()
    assert r_part["role"] == "participated"
    assert r_part["total"] == part["participated_total"]
    assert len(r_part["rows"]) == min(part["participated_total"], 500)
    assert all(row["credit_position"] >= 1 for row in r_part["rows"])
    assert any(row["credit_position"] > 1 for row in r_part["rows"])

    r_prime = client.get("/api/buses/terran/provenance?metric=fleet").json()
    assert r_prime["role"] == "prime"
    assert r_prime["total"] == d["benchmark"]["fleet_total"]


@pytest.mark.db
def test_participated_role_contract(db_conn):
    """422 walls: participated receipts are a manufacturer fleet-membership claim only."""
    client = _client()
    assert client.get("/api/buses/terran/provenance?role=bogus").status_code == 422
    assert (client.get("/api/buses/terran/provenance?metric=active&role=participated")
            .status_code == 422)
    # A bus-model cohort has no co-builder semantics (kind=bus pins the shadowed cohort).
    with db_conn.cursor() as cur:
        cur.execute("SELECT bus_slug FROM v_bus_benchmarks_bus LIMIT 1")
        bus_slug = cur.fetchone()[0]
    assert (client.get(f"/api/buses/{bus_slug}/provenance?kind=bus&role=participated")
            .status_code == 422)
    # Bus-kind detail payloads carry participation: null rather than a number.
    r = client.get(f"/api/buses/{bus_slug}?kind=bus")
    assert r.status_code == 200 and r.json()["participation"] is None
