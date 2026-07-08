"""DB-backed matcher tests. Synthetic raw rows use the reserved NORAD range 910000001-919999999.

Nothing is committed; the db_conn fixture rolls back on close, so no synthetic row survives.
"""

import csv

import pytest

from identity import match

pytestmark = pytest.mark.db


def _new_run(cur, source, status="ok"):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
        "VALUES (%s, 'test://t4', now(), now(), %s) RETURNING ingest_run_id",
        (source, status),
    )
    return cur.fetchone()[0]


def _satcat(cur, run, norad, name, object_id, launch, perigee, apogee, owner="US",
            obj_type="PAYLOAD", ops="+"):
    cur.execute(
        "INSERT INTO raw_satcat (norad_cat_id, object_name, object_id, object_type, "
        "ops_status_code, owner, launch_date, perigee, apogee, ingest_run_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (norad, name, object_id, obj_type, ops, owner, launch, perigee, apogee, run),
    )


def _gcat(cur, run, jcat, norad, piece, name, launch, perigee=None, apogee=None, state=None):
    cur.execute(
        "INSERT INTO raw_gcat_satcat (jcat, norad_id, piece, name, pl_name, launch_date, "
        "perigee_km, apogee_km, state, ingest_run_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (jcat, norad, piece, name, name, launch, perigee, apogee, state, run),
    )


def _ucs(cur, run, row_key, name, launch, norad=None, cospar=None, country=None):
    cur.execute(
        "INSERT INTO raw_ucs (row_key, name, norad_id, cospar_id, launch_date, "
        "country_operator, ingest_run_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (row_key, name, norad, cospar, launch, country, run),
    )


def _identifiers(cur, satellite_id):
    cur.execute(
        "SELECT id_type, id_value, source FROM satellite_identifier WHERE satellite_id = %s",
        (satellite_id,),
    )
    return {(t, v, s) for t, v, s in cur.fetchall()}


def _sat_id_by_norad(cur, norad):
    cur.execute("SELECT satellite_id FROM satellite WHERE norad_id = %s", (norad,))
    row = cur.fetchone()
    return row[0] if row else None


def test_deterministic_norad_and_cospar_linking(db_conn):
    with db_conn.cursor() as cur:
        srun = _new_run(cur, "satcat")
        _satcat(cur, srun, 910000001, "STARLINK-30042", "2023-054A", "2023-05-01", 540, 560)
        grun = _new_run(cur, "gcat")
        _gcat(cur, grun, "S00001", 910000001, "2023-054A", "Starlink 30042", "2023 May 1")
        _gcat(cur, grun, "S00002", None, "2023-060B", "Analyst Object", "2023 Jun 1")

    match.deterministic(db_conn)

    with db_conn.cursor() as cur:
        sat = _sat_id_by_norad(cur, 910000001)
        assert sat is not None
        ids = _identifiers(cur, sat)
        assert ("norad", "910000001", "satcat") in ids
        assert ("cospar", "2023-054A", "satcat") in ids
        assert ("name_satcat", "STARLINK-30042", "satcat") in ids
        assert ("gcat_id", "S00001", "gcat") in ids
        assert ("name_gcat", "Starlink 30042", "gcat") in ids
        # NORAD-less GCAT row linked by exact COSPAR into its own satellite
        cur.execute(
            "SELECT satellite_id FROM satellite_identifier "
            "WHERE id_type='cospar' AND id_value='2023-060B'"
        )
        cospar_sat = cur.fetchone()
        assert cospar_sat is not None
        assert ("gcat_id", "S00002", "gcat") in _identifiers(cur, cospar_sat[0])
    db_conn.rollback()


def test_cospar_ambiguity_links_lowest_satellite_id_and_is_counted(db_conn):
    """Finding #7: when a COSPAR maps to more than one satellite, a NORAD-less piece must resolve
    deterministically (lowest satellite_id, not run-dependent), and the ambiguity must be counted
    as a DQ signal in the build stats."""
    with db_conn.cursor() as cur:
        srun = _new_run(cur, "satcat")
        # Two DIFFERENT physical objects sharing one COSPAR -> that COSPAR maps to 2 satellites.
        _satcat(cur, srun, 910000401, "AMBIG-A", "2023-500A", "2023-05-01", 540, 560)
        _satcat(cur, srun, 910000402, "AMBIG-B", "2023-500A", "2023-05-01", 540, 560)
        grun = _new_run(cur, "gcat")
        # NORAD-less GCAT piece carrying the same standard COSPAR -> resolved via the COSPAR pass.
        _gcat(cur, grun, "G-AMBIG", None, "2023-500A", "Ambiguous Piece", "2023 May 1")

    stats = match.deterministic(db_conn)

    assert stats["ambiguous_cospar_links"] >= 1
    with db_conn.cursor() as cur:
        low = _sat_id_by_norad(cur, 910000401)
        high = _sat_id_by_norad(cur, 910000402)
        assert low < high
        cur.execute(
            "SELECT satellite_id FROM satellite_identifier "
            "WHERE id_type='gcat_id' AND id_value='G-AMBIG'"
        )
        assert cur.fetchone()[0] == low, "ambiguous COSPAR must resolve to the lowest satellite_id"
    db_conn.rollback()


def test_probabilistic_true_positive_autolinks(db_conn, tmp_path):
    review = tmp_path / "review.csv"
    with db_conn.cursor() as cur:
        srun = _new_run(cur, "satcat")
        _satcat(cur, srun, 910000010, "STARLINK-30042", "2023-054A", "2023-05-01", 540, 560)
        urun = _new_run(cur, "ucs")
        _ucs(cur, urun, "UCS-TP", "Starlink 30042", "2023-05-03", country="USA")

    match.run_matchers(db_conn, review_csv=review)

    with db_conn.cursor() as cur:
        sat = _sat_id_by_norad(cur, 910000010)
        cur.execute(
            "SELECT confidence FROM satellite_identifier "
            "WHERE id_type='ucs_row' AND id_value='UCS-TP' AND satellite_id=%s",
            (sat,),
        )
        row = cur.fetchone()
        assert row is not None, "UCS row should auto-link to the Starlink satellite"
        assert 0.92 <= float(row[0]) <= 1.0
        cur.execute(
            "SELECT count(*) FROM merge_log WHERE rule_fired='name_fuzzy>=0.92' AND surviving_id=%s",
            (sat,),
        )
        assert cur.fetchone()[0] >= 1
    assert not review.exists(), "an auto-link must not land in the review queue"
    db_conn.rollback()


def test_probabilistic_true_negative_blocked_by_regime_gate(db_conn, tmp_path):
    review = tmp_path / "review.csv"
    with db_conn.cursor() as cur:
        srun = _new_run(cur, "satcat")
        # GEO comsat, identical normalized name to the LEO probe below
        _satcat(cur, srun, 910000020, "STARLINK-5", "2023-070A", "2023-01-01", 35786, 35786)
        grun = _new_run(cur, "gcat")
        # NORAD-less, non-standard piece -> probabilistic; LEO altitudes; near-identical name
        _gcat(cur, grun, "G-TN", None, "", "Starlink 5", "2023 Jan 2",
              perigee=540, apogee=560, state="US")

    match.run_matchers(db_conn, review_csv=review)

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM satellite_identifier WHERE id_value='G-TN'")
        assert cur.fetchone()[0] == 0, "regime mismatch must block the link"
    assert not review.exists()
    db_conn.rollback()


def test_probabilistic_borderline_goes_to_review_csv(db_conn, tmp_path):
    review = tmp_path / "review.csv"
    with db_conn.cursor() as cur:
        srun = _new_run(cur, "satcat")
        _satcat(cur, srun, 910000030, "ONEWEB-0012", "2023-080A", "2023-06-01", 1180, 1200,
                owner="UK")
        urun = _new_run(cur, "ucs")
        _ucs(cur, urun, "UCS-BORD", "OneWeb 0099", "2023-06-10", country="UK")

    match.run_matchers(db_conn, review_csv=review)

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM satellite_identifier WHERE id_value='UCS-BORD'")
        assert cur.fetchone()[0] == 0, "borderline score must NOT auto-link"
    assert review.exists(), "borderline match should be parked for human review"
    with review.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    score = float(rows[0]["score"])
    assert 0.75 <= score < 0.92, f"review score {score} outside the review band"
    db_conn.rollback()
