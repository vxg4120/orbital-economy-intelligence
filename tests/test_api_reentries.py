"""GET /api/reentries/upcoming (docs/specs/spacetrack-reentry-predictions.md).

The SQL test shadows the view and the graph tables with TEMP tables on its own connection, so it
needs no seeded graph and leaves the shared dev database untouched.
"""

import datetime as dt
import warnings

import pytest
from psycopg.rows import dict_row

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app
from api.routers.reentries import WINDOW_DAYS, build_upcoming, counts

TODAY = dt.date(2030, 1, 3)


@pytest.mark.db
def test_upcoming_shape_on_the_real_schema(db_conn):
    r = TestClient(app).get("/api/reentries/upcoming")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"as_of", "pulled_at", "window_days", "predicted_through", "counts",
                         "rows"}
    assert body["window_days"] == WINDOW_DAYS == 60
    assert set(body["counts"]) == {"payload", "rocket_body", "debris", "unknown", "starlink"}


@pytest.mark.db
def test_the_window_the_drops_and_the_order(db_conn):
    db_conn.row_factory = dict_row
    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE ingest_run (ingest_run_id int, source text, endpoint text,
                status text, started_at timestamptz, rows_ingested int);
            INSERT INTO ingest_run VALUES
                (1, 'spacetrack', 'decay_60day', 'ok', '2029-12-26 19:10Z', 3),
                (2, 'spacetrack', 'decay_60day', 'ok', '2030-01-02 19:10Z', 7),
                (3, 'spacetrack', 'decay_60day', 'error', '2030-01-03 07:10Z', 0),
                (4, 'spacetrack', 'decay', 'ok', '2030-01-03 07:10Z', 40);
            CREATE TEMP TABLE v_reentry_prediction_latest (norad_cat_id bigint,
                satellite_id int, object_name text, predicted_date date, msg_epoch timestamptz);
            INSERT INTO v_reentry_prediction_latest VALUES
                (101, 1, 'SL-4 R/B', '2030-01-10', '2030-01-01 12:00Z'),
                (102, 2, 'STARLINK-1001', '2030-01-20', '2030-01-01 12:00Z'),
                (103, 3, 'TESTSAT', '2030-01-25', '2030-01-02 06:00Z'),
                (104, NULL, 'NEW OBJECT', '2030-01-05', '2030-01-01 12:00Z'),
                (105, 5, 'ALREADY DOWN', '2030-01-06', '2030-01-01 12:00Z'),
                (106, 6, 'PAST DATE', '2030-01-02', '2030-01-01 12:00Z'),
                (107, 7, 'TOO FAR', '2030-03-05', '2030-01-01 12:00Z'),
                (108, 8, 'LAST DAY', '2030-03-04', '2030-01-01 12:00Z'),
                (109, 9, 'NO DATE', NULL, '2030-01-01 12:00Z');
            CREATE TEMP TABLE satellite (satellite_id int, canonical_name text, object_type text,
                decay_date date);
            INSERT INTO satellite VALUES
                (1, 'SL-4 R/B', 'ROCKET_BODY', NULL), (2, 'STARLINK-1001', 'PAYLOAD', NULL),
                (3, 'TestSat', 'PAYLOAD', NULL), (5, 'Down', 'PAYLOAD', '2030-01-01'),
                (6, 'Past', 'PAYLOAD', NULL), (7, 'Far', 'DEBRIS', NULL),
                (8, 'Last day', 'DEBRIS', NULL), (9, 'No date', 'PAYLOAD', NULL);
            CREATE TEMP TABLE operator (operator_id int, canonical_name text,
                operator_class text);
            CREATE TEMP TABLE satellite_operator (satellite_id int, operator_id int, role text,
                valid_to date, confidence real);
            INSERT INTO operator VALUES (1, 'Acme Space', 'private');
            INSERT INTO satellite_operator VALUES (3, 1, 'owner', NULL, 1.0);
        """)
    body = build_upcoming(db_conn, today=TODAY)

    assert body["as_of"] == "2030-01-02"
    assert body["pulled_at"].startswith("2030-01-02T19:10")  # the newest OK pull, not the error
    assert body["predicted_through"] == "2030-03-04"
    # Payloads first, then by predicted date; decayed, past, undated and too-far rows dropped.
    assert [r["norad_id"] for r in body["rows"]] == [102, 103, 104, 101, 108]
    by_id = {r["norad_id"]: r for r in body["rows"]}
    assert by_id[103] == {"norad_id": 103, "satellite_id": 3, "name": "TestSat",
                          "object_type": "PAYLOAD", "owner": "Acme Space",
                          "predicted_date": "2030-01-25",
                          "msg_epoch": "2030-01-02T06:00:00+00:00"}
    assert by_id[104]["satellite_id"] is None and by_id[104]["name"] == "NEW OBJECT"
    assert body["counts"] == {"payload": 2, "rocket_body": 1, "debris": 1, "unknown": 1,
                              "starlink": 1}


@pytest.mark.db
def test_before_the_first_pull_the_set_is_empty(db_conn):
    db_conn.row_factory = dict_row
    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE ingest_run (ingest_run_id int, source text, endpoint text,
                status text, started_at timestamptz, rows_ingested int);
            CREATE TEMP TABLE v_reentry_prediction_latest (norad_cat_id bigint,
                satellite_id int, object_name text, predicted_date date, msg_epoch timestamptz);
        """)
    body = build_upcoming(db_conn, today=TODAY)
    assert (body["as_of"], body["pulled_at"], body["rows"]) == (None, None, [])


def test_counts_name_starlink_payloads_only():
    rows = [{"object_type": "PAYLOAD", "name": "Starlink-31000"},
            {"object_type": "DEBRIS", "name": "STARLINK-1 DEB"},
            {"object_type": None, "name": None}]
    assert counts(rows) == {"payload": 1, "rocket_body": 0, "debris": 1, "unknown": 1,
                            "starlink": 1}


@pytest.mark.db
def test_the_real_view_finds_a_satellite_by_its_canonical_norad_number(db_conn):
    """A satellite GCAT catalogued before SATCAT has its NORAD number but no SATCAT identifier.
    The view finds it by satellite.norad_id, so a decayed one stays off the list and a live
    one keeps its resolver link. One transaction, rolled back."""
    db_conn.row_factory = dict_row
    try:
        with db_conn.cursor() as cur:
            cur.execute("INSERT INTO ingest_run (source, endpoint, started_at, finished_at, "
                        "status, rows_ingested) VALUES ('spacetrack', 'decay_60day', now(), "
                        "now(), 'ok', 3) RETURNING ingest_run_id")
            run = cur.fetchone()["ingest_run_id"]
            sats = {}
            for norad, decayed in ((900000701, "2030-01-01"), (900000702, None)):
                cur.execute("INSERT INTO satellite (norad_id, canonical_name, object_type, "
                            "decay_date) VALUES (%s, %s, 'PAYLOAD', %s) RETURNING satellite_id",
                            (norad, f"GCAT ONLY {norad}", decayed))
                sats[norad] = cur.fetchone()["satellite_id"]
            for norad in (900000701, 900000702, 900000703):
                cur.execute("INSERT INTO raw_spacetrack_decay_prediction (norad_cat_id, "
                            "object_name, msg_epoch, decay_epoch, decay_date, source, payload, "
                            "ingest_run_id) VALUES (%s, %s, '2030-01-02 06:00Z', '2030-01-20', "
                            "'2030-01-20', '60day_msg', '{}', %s)",
                            (norad, f"ST {norad}", run))
        body = build_upcoming(db_conn, today=TODAY)
        assert [(r["norad_id"], r["satellite_id"], r["name"]) for r in body["rows"]] == [
            (900000702, sats[900000702], "GCAT ONLY 900000702"),
            (900000703, None, "ST 900000703"),
        ]
        assert body["as_of"] == "2030-01-02"
    finally:
        db_conn.row_factory = None
        db_conn.rollback()
