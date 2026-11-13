"""Per-operator congestion exposure (docs/specs/congestion-exposure.md).

The view's own SELECT runs as a TEMP view over TEMP tables, which shadow the real relations for
this connection only, so the tests seed nothing in the shared database. Every expected number is
worked out by hand in the comments.
"""

import warnings

import pytest
from fastapi import FastAPI
from psycopg.rows import dict_row

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.deps import get_db
from api.routers import congestion, operators
from tests.test_metrics import _view_statement

ALPHA, BETA, GAMMA, DELTA, EPSILON, THETA, IOTA = 10, 20, 30, 40, 45, 50, 60

# Shell A: 550 km mean altitude (bin 11), 53 deg (bin 10). Shell B: 550 km, 97 deg (bin 19).
# Shell C: GEO, 35,785 km (bin 715), 0 deg (bin 0).
_SEED = f"""
    CREATE TEMP TABLE mv_latest_gp_element (
        norad_id bigint, epoch timestamptz, inclination float8,
        perigee_km float8, apogee_km float8);
    CREATE TEMP TABLE satellite (
        satellite_id bigint PRIMARY KEY, norad_id bigint UNIQUE, decay_date date,
        cospar_id text, canonical_name text, object_type text);
    CREATE TEMP TABLE operator (
        operator_id bigint PRIMARY KEY, canonical_name text, country text, operator_class text);
    CREATE TEMP TABLE satellite_operator (
        satellite_id bigint, operator_id bigint, role text, valid_to date);
    INSERT INTO mv_latest_gp_element VALUES
        (1, '2026-11-12 10:00+00', 53.0, 540, 560),   -- A, ALPHA
        (2, '2026-11-12 10:00+00', 53.0, 540, 560),   -- A, ALPHA
        (3, '2026-11-12 10:00+00', 53.0, 540, 560),   -- A, ALPHA
        (4, '2026-11-12 10:00+00', 53.0, 540, 560),   -- A, BETA
        (9, '2026-11-12 10:00+00', 53.0, 540, 560),   -- A, GAMMA
        (13, '2026-11-12 10:00+00', 53.0, 540, 560),  -- A, THETA
        (14, '2026-11-12 10:00+00', 53.0, 540, 560),  -- A, IOTA
        (5, '2026-11-12 10:00+00', 53.0, 540, 560),   -- A, a satellite with no owner
        (6, '2026-11-12 10:00+00', 53.0, 540, 560),   -- A, no satellite row at all
        (7, '2026-11-12 10:00+00', 97.0, 540, 560),   -- B, ALPHA
        (8, '2026-11-12 10:00+00', 97.0, 540, 560),   -- B, BETA
        (10, '2026-11-12 10:00+00', 97.0, 540, 560),  -- B, BETA
        (11, '2026-11-12 10:00+00', 0.1, 35780, 35790),  -- C, DELTA alone
        (12, '2026-10-20 06:00+00', 53.0, 180, 200);  -- reentered, EPSILON
    INSERT INTO satellite (satellite_id, norad_id, decay_date) VALUES
        (1, 1, NULL), (2, 2, NULL), (3, 3, NULL), (4, 4, NULL), (5, 5, NULL), (7, 7, NULL),
        (8, 8, NULL), (9, 9, NULL), (10, 10, NULL), (11, 11, NULL), (12, 12, '2026-11-01'),
        (13, 13, NULL), (14, 14, NULL);
    INSERT INTO operator (operator_id, canonical_name) VALUES
        ({ALPHA}, 'ALPHA'), ({BETA}, 'BETA'), ({GAMMA}, 'GAMMA'), ({DELTA}, 'DELTA'),
        ({EPSILON}, 'EPSILON'), ({THETA}, 'THETA'), ({IOTA}, 'IOTA');
    INSERT INTO satellite_operator VALUES
        (1, {ALPHA}, 'owner', NULL), (2, {ALPHA}, 'owner', NULL), (3, {ALPHA}, 'owner', NULL),
        (7, {ALPHA}, 'owner', NULL), (4, {BETA}, 'owner', NULL), (8, {BETA}, 'owner', NULL),
        (10, {BETA}, 'owner', NULL), (9, {GAMMA}, 'owner', NULL), (13, {THETA}, 'owner', NULL),
        (14, {IOTA}, 'owner', NULL), (11, {DELTA}, 'owner', NULL),
        (12, {EPSILON}, 'owner', NULL);
"""


@pytest.fixture
def field(db_conn):
    db_conn.row_factory = dict_row
    body = _view_statement("v_congestion_exposure").replace(
        "CREATE OR REPLACE VIEW", "CREATE TEMP VIEW", 1
    )
    with db_conn.cursor() as cur:
        cur.execute(_SEED)
        cur.execute(body)
    try:
        yield db_conn
    finally:
        db_conn.rollback()


@pytest.mark.db
def test_alpha_shares_its_shells_mostly_with_others(field):
    a = congestion._build_operator_exposure(field)[ALPHA]
    # ALPHA has 4 satellites in the field: 3 in shell A (9 objects) and 1 in shell B (3 objects),
    # so its weights are 3/4 and 1/4.
    assert a["fleet_in_field"] == 4
    assert a["shells"] == 2
    # Neighbours: 3/4 * (9 - 1) + 1/4 * (3 - 1) = 6 + 0.5.
    assert a["neighbours_per_satellite"] == pytest.approx(6.5)
    # Own: 3/4 * (3 - 1) + 1/4 * (1 - 1).
    assert a["own_per_satellite"] == pytest.approx(1.5)
    # Others: 3/4 * (9 - 3) + 1/4 * (3 - 1) = 4.5 + 0.5.
    assert a["others_per_satellite"] == pytest.approx(5.0)
    # Named: BETA 3/4 * 1 + 1/4 * 2 = 1.25; GAMMA, THETA and IOTA 3/4 * 1 = 0.75 each, so 3.5.
    # Unattributed: the ownerless satellite 5 and the rowless object 6 in A, 3/4 * 2 = 1.5.
    assert a["unattributed_per_satellite"] == pytest.approx(1.5)
    assert a["own_per_satellite"] + a["others_per_satellite"] == pytest.approx(
        a["neighbours_per_satellite"])
    # The top list is capped at three, largest first, ties broken by operator id.
    assert [(n["operator_id"], round(n["per_satellite"], 2)) for n in a["top_neighbours"]] == [
        (BETA, 1.25), (GAMMA, 0.75), (THETA, 0.75)]
    assert a["busiest_shell"] == {
        "alt_km_from": 550, "alt_km_to": 600, "inc_deg_from": 50, "inc_deg_to": 55,
        "operator_objects": 3, "objects": 9,
    }
    # 9 + 3 + 1 objects in the field; the reentered object 12 is not one of them.
    assert a["field_objects"] == 13
    # The view's score is (neighbours + 1) / field, as the spec says.
    assert a["exposure_score"] == pytest.approx((6.5 + 1) / 13)


@pytest.mark.db
def test_an_operator_alone_in_its_shell_has_no_neighbours(field):
    d = congestion._build_operator_exposure(field)[DELTA]
    assert d["fleet_in_field"] == 1
    assert d["neighbours_per_satellite"] == 0
    assert d["own_per_satellite"] == 0
    assert d["others_per_satellite"] == 0
    assert d["unattributed_per_satellite"] == 0
    assert d["top_neighbours"] == []
    assert d["busiest_shell"]["alt_km_from"] == 35750
    assert d["busiest_shell"]["objects"] == 1


@pytest.mark.db
def test_the_detail_endpoint_carries_the_block_or_null(field):
    app = FastAPI()
    app.include_router(operators.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: field
    client = TestClient(app)

    alpha = client.get(f"/api/operators/{ALPHA}").json()["congestion"]
    assert alpha["fleet_in_field"] == 4
    assert alpha["top_neighbours"][0]["canonical_name"] == "BETA"
    # EPSILON's only object reentered, so none of its objects is in the field.
    assert client.get(f"/api/operators/{EPSILON}").json()["congestion"] is None
