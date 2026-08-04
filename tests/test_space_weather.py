"""Space weather layer: CelesTrak indices landing, the drag join, and the API surface.

The physical claim this layer publishes — storms drag the whole LEO fleet — rides on three
mechanical guarantees tested here: the CSV lands verbatim under an explicit header map (a
format change on CelesTrak's side must raise, not silently drop columns), the daily view's
derived fields (kp_max rescaling, G-scale mapping) stay consistent with the raw indices, and
the drag view never publishes a day thin enough to be an artifact. Historical index values
are frozen physics (the Kp of a past day never changes), so exact pins on known storm days
are legitimate where fleet counts would not be.
"""

import datetime as dt
import warnings

import pytest

from ingest import celestrak_sw

_CSV_HEADER = (
    "DATE,BSRN,ND,KP1,KP2,KP3,KP4,KP5,KP6,KP7,KP8,KP_SUM,AP1,AP2,AP3,AP4,AP5,AP6,AP7,AP8,"
    "AP_AVG,CP,C9,ISN,F10.7_OBS,F10.7_ADJ,F10.7_DATA_TYPE,F10.7_OBS_CENTER81,"
    "F10.7_OBS_LAST81,F10.7_ADJ_CENTER81,F10.7_ADJ_LAST81"
)

_OBS_ROW = ("2026-08-02,2632,21,20,17,23,57,40,33,27,30,247,7,6,9,67,27,18,12,15,17,1.1,5,"
            "128,126.9,128.4,OBS,134.0,131.2,135.8,132.9")
_PRD_ROW = "2026-08-05,2632,24,13,,,,,,,,13,5,,,,,,,,5,,,95,111.7,113.1,PRD,133.5,130.0,,"


def _client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def test_parse_lands_kp_in_file_units_and_types_cells():
    rows = celestrak_sw.parse_rows(_CSV_HEADER + "\n" + _OBS_ROW + "\n" + _PRD_ROW + "\n")
    assert len(rows) == 2
    obs, prd = rows
    assert obs["sw_date"] == dt.date(2026, 8, 2)
    assert obs["kp4"] == 57 and obs["ap4"] == 67  # Kp x 10 verbatim; Ap in real units
    assert obs["cp"] == 1.1 and obs["f107_obs"] == 126.9
    assert obs["f107_data_type"] == "OBS"
    # Predicted rows land too, with their blanks as NULLs, because the forward view is data.
    assert prd["f107_data_type"] == "PRD"
    assert prd["kp2"] is None and prd["ap_avg"] == 5


def test_parse_raises_on_unknown_header_and_drops_dateless_rows():
    with pytest.raises(ValueError, match="unknown headers"):
        celestrak_sw.parse_rows(_CSV_HEADER + ",SURPRISE\n")
    rows = celestrak_sw.parse_rows(_CSV_HEADER + "\n" + ",,,,,,,,,,,,,,,,,,,,,,,,,,,,,,\n")
    assert rows == []


def test_parse_degrades_malformed_cells_to_null():
    bad = _OBS_ROW.replace("126.9", "n/a").replace("2632", "??")
    rows = celestrak_sw.parse_rows(_CSV_HEADER + "\n" + bad + "\n")
    assert rows[0]["f107_obs"] is None and rows[0]["bsrn"] is None
    assert rows[0]["sw_date"] == dt.date(2026, 8, 2)  # the rest of the row survives


@pytest.mark.db
def test_daily_view_is_one_row_per_day_with_consistent_derivations(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT day) FROM v_space_weather_daily")
        total, distinct = cur.fetchone()
        assert total > 20000 and total == distinct, "one row per day, 1957 onward"
        # kp_max must equal the greatest 3-hourly value rescaled; check as a set predicate.
        cur.execute(
            """
            SELECT count(*) FROM v_space_weather_daily v
            JOIN raw_celestrak_sw r ON r.sw_date = v.day AND r.ingest_run_id = (
                SELECT max(r2.ingest_run_id) FROM raw_celestrak_sw r2
                JOIN ingest_run i ON i.ingest_run_id = r2.ingest_run_id
                WHERE i.status = 'ok')
            WHERE v.kp_max * 10.0 <> GREATEST(r.kp1, r.kp2, r.kp3, r.kp4,
                                              r.kp5, r.kp6, r.kp7, r.kp8)
            """
        )
        assert cur.fetchone()[0] == 0
        # The G scale and kp_max must agree everywhere (mapping is monotone in kp_max).
        cur.execute(
            """
            SELECT count(*) FROM v_space_weather_daily
            WHERE (storm_level IS NULL AND kp_max >= 5.0)
               OR (storm_level IS NOT NULL AND kp_max < 5.0)
               OR (storm_level = 'G5' AND kp_max < 9.0)
            """
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_known_storm_days_pin_exactly(db_conn):
    """Historical index values are frozen: the 2026-08-02 G1 (Kp 5.7, 3-hourly Ap peak 67)
    and the 2026-01-20 G3 (Ap daily average 144) are facts of record in the landed file."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT round(kp_max, 1), ap_max, storm_level FROM v_space_weather_daily "
            "WHERE day = '2026-08-02'"
        )
        kp, ap_max, level = cur.fetchone()
        assert (float(kp), ap_max, level) == (5.7, 67, "G1")
        cur.execute(
            "SELECT ap_avg, storm_level FROM v_space_weather_daily WHERE day = '2026-01-20'"
        )
        ap_avg, level = cur.fetchone()
        assert ap_avg == 144 and level == "G3"


@pytest.mark.db
def test_drag_view_publishes_no_thin_days(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(sats_observed), max(abs(median_dsma_m)) FROM v_drag_daily"
        )
        days, min_sats, worst = cur.fetchone()
    assert days > 200, "drag series collapsed; check sat_daily coverage"
    assert min_sats >= 500, "a thin day leaked past the sample-size floor"
    assert worst < 10000, "a published fleet median beyond 10 km/day is an artifact"


@pytest.mark.db
def test_environment_api_contract(db_conn):
    client = _client()
    r = client.get("/api/environment?days=30").json()
    assert {"rows", "latest_observed", "worst_drag_day", "note", "attribution"} <= set(r)
    assert len(r["rows"]) > 20
    row = r["rows"][0]
    assert {"day", "kp_max", "ap_avg", "storm_level", "median_dsma_m",
            "f107_data_type"} <= set(row)
    # Predictions are an index forward view, never a drag measurement.
    assert all(x["median_dsma_m"] is None
               for x in r["rows"] if x["f107_data_type"] == "PRD")
    assert client.get("/api/environment?days=3").status_code == 422
    assert client.get("/api/environment?forward=99").status_code == 422
    # The API's worst day must agree with the view it claims to serve.
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT day::text, median_dsma_m FROM v_drag_environment_daily "
            "WHERE median_dsma_m IS NOT NULL AND day >= current_date - 30 "
            "ORDER BY median_dsma_m ASC LIMIT 1"
        )
        db_worst = cur.fetchone()
    if db_worst is not None:
        assert str(r["worst_drag_day"]["day"]) == db_worst[0]
        assert float(r["worst_drag_day"]["median_dsma_m"]) == float(db_worst[1])


@pytest.mark.db
def test_report_carries_the_section_with_no_duplicate_numbering(db_conn):
    from quality.report import generate_report

    content = generate_report(db_conn)
    assert "## 9. Space weather and the drag environment" in content
    assert "## 8. Phase 2 metrics" in content
    # The renumbering bug this pass fixed: two sections both numbered 7.
    assert content.count("\n## 7. ") == 1
