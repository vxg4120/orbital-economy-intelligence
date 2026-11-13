"""GET /api/congestion: which objects the density field counts.

The db-marked test shadows mv_latest_gp_element and satellite with TEMP tables, which live only
for this connection, so it seeds nothing in the shared database.
"""

import warnings

import pytest
from fastapi import FastAPI
from psycopg.rows import dict_row

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.deps import get_db
from api.routers import congestion


@pytest.mark.db
def test_reentered_objects_leave_the_field(db_conn):
    """Audit 2026-11-13: the heatmap counted 863 objects that had already reentered, because the
    latest-element view keeps an object's last element set after it decays. 835 of them sat
    below 300 km, which drew a bright band where nothing stays up for long. An object drops out
    when its recorded decay falls on or after the UTC day of its latest element set; an element
    set newer than the decay date means the date is wrong, so that object stays."""
    db_conn.row_factory = dict_row
    with db_conn.cursor() as cur:
        # The session zone is west of UTC on purpose: 02:00 UTC on Nov 4 is still Nov 3 there,
        # and the filter must compare UTC days.
        cur.execute("""
            SET LOCAL TIME ZONE 'America/Los_Angeles';
            CREATE TEMP TABLE mv_latest_gp_element (
                norad_id bigint, epoch timestamptz, inclination float8,
                perigee_km float8, apogee_km float8);
            CREATE TEMP TABLE satellite (norad_id bigint UNIQUE, decay_date date);
            INSERT INTO mv_latest_gp_element VALUES
                (1, '2026-11-12 10:00+00', 53.0, 540, 560),  -- in orbit, no decay date
                (2, '2026-11-03 06:00+00', 53.0, 150, 170),  -- decayed the same day
                (3, '2026-10-20 06:00+00', 53.0, 180, 200),  -- decayed after its last set
                (4, '2026-11-12 10:00+00', 97.0, 540, 560),  -- decay date older than its set
                (5, '2026-11-04 02:00+00', 97.0, 540, 560),  -- decay on the UTC day before
                (6, '2026-11-12 10:00+00', 53.0, 540, 560),  -- no satellite row at all
                (7, '2026-11-12 10:00+00', 0.1, 35780, 35790);  -- GEO, outside the field
            INSERT INTO satellite VALUES
                (1, NULL), (2, '2026-11-03'), (3, '2026-11-01'), (4, '2021-03-18'),
                (5, '2026-11-03'), (7, NULL);
        """)
    app = FastAPI()
    app.include_router(congestion.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_conn
    try:
        bins = TestClient(app).get("/api/congestion").json()["bins"]
    finally:
        db_conn.rollback()

    assert sorted((b["alt_bin_km"], b["inc_bin_deg"], b["object_count"]) for b in bins) == [
        (550, 50, 2),  # 1 and 6
        (550, 95, 2),  # 4 and 5
    ]
