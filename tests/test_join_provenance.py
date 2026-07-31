"""Join provenance (tenancy Phase 3): every attribution row says how it was joined.

Three categorical rules instead of a confidence float: anchored_norad (the row carries the
permanent anchor), anchored_cospar (crosswalk to an anchored satellite), provisional_slot (a
dated occupancy observation, kept on the leaderboard and flagged rather than hidden, because
withholding provisional fleets would blind the product to every fleet younger than about a
month). key_churn_observed marks joins that rode a key the churn ledger has seen move.
"""

import pytest


@pytest.mark.db
def test_every_row_carries_a_valid_join_rule(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT join_rule, count(*) FROM satellite_bus GROUP BY 1 ORDER BY 2 DESC"
        )
        dist = dict(cur.fetchall())
    assert set(dist) <= {"anchored_norad", "anchored_cospar", "provisional_slot",
                         "operator_confirmed"}
    assert dist.get("anchored_norad", 0) > 20000, "the anchored majority rides direct norad joins"


@pytest.mark.db
def test_anchored_norad_joins_never_flag_churn(db_conn):
    """The key that produces an anchored_norad join has never been observed to move, so churn on
    the row's piece is irrelevant to the join and must not be flagged onto it."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM satellite_bus "
            "WHERE join_rule = 'anchored_norad' AND key_churn_observed"
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_join_rule_matches_anchor_state(db_conn):
    """anchored_* rows sit on anchored satellites; provisional_slot rows sit on provisional
    satellites. A mismatch means the resolver and the anchor bookkeeping disagree."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM satellite_bus sb
            JOIN satellite s USING (satellite_id)
            WHERE (sb.join_rule LIKE 'anchored%' AND s.anchor_state <> 'anchored')
               OR (sb.join_rule = 'provisional_slot' AND s.anchor_state <> 'provisional')
            """
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_provisional_n_sums_to_provisional_rows(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT COALESCE(sum(provisional_n), 0) FROM v_bus_benchmarks_manufacturer")
        via_view = int(cur.fetchone()[0])
        cur.execute(
            "SELECT count(*) FROM satellite_bus "
            "WHERE join_rule = 'provisional_slot' AND manufacturer_slug IS NOT NULL"
        )
        via_rows = cur.fetchone()[0]
    assert via_view == via_rows


@pytest.mark.db
def test_anchored_views_share_columns_with_base(db_conn):
    """The _anchored variants are textual duplicates of the base views; this parity check is
    what stops the pairs from drifting when one body is edited."""
    with db_conn.cursor() as cur:
        for base in ("v_bus_benchmarks_manufacturer", "v_bus_benchmarks_bus"):
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position", (base,)
            )
            base_cols = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position", (base + "_anchored",)
            )
            anchored_cols = [r[0] for r in cur.fetchall()]
            assert base_cols == anchored_cols, f"{base}_anchored drifted from {base}"


@pytest.mark.db
def test_anchored_view_excludes_exactly_the_provisional_rows(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT COALESCE(sum(fleet_total), 0) FROM v_bus_benchmarks_manufacturer), "
            "(SELECT COALESCE(sum(fleet_total), 0) FROM v_bus_benchmarks_manufacturer_anchored), "
            "(SELECT count(*) FROM satellite_bus WHERE join_rule = 'provisional_slot' "
            " AND manufacturer_slug IS NOT NULL)"
        )
        all_sum, anchored_sum, provisional = cur.fetchone()
    assert int(all_sum) - int(anchored_sum) == provisional


@pytest.mark.db
def test_state_filter_contract(db_conn):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)
    r_all = client.get("/api/buses?limit=1")
    assert r_all.status_code == 200 and r_all.json()["state"] == "all"
    assert "provisional_n" in r_all.json()["rows"][0]
    r_anch = client.get("/api/buses?limit=1&state=anchored")
    assert r_anch.status_code == 200 and r_anch.json()["state"] == "anchored"
    assert client.get("/api/buses?state=bogus").status_code == 422
    # With an empty provisional set the two states must agree exactly.
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM satellite_bus WHERE join_rule = 'provisional_slot'")
        provisional = cur.fetchone()[0]
    if provisional == 0:
        assert r_all.json()["rows"][0]["fleet_total"] == r_anch.json()["rows"][0]["fleet_total"]


@pytest.mark.db
def test_v_bus_sat_exposes_join_provenance(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT join_rule, key_churn_observed FROM v_bus_sat LIMIT 1")
        row = cur.fetchone()
    assert row is not None
