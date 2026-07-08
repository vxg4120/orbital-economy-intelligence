"""End-to-end pipeline test on a tiny synthetic SATCAT + GCAT fixture (reserved NORAD range).

5 objects: 3 matched by NORAD (one carrying a SATCAT/GCAT status disagreement), 1 GCAT-only
analyst object (linked by COSPAR), 1 SATCAT-only. Runs build_graph.run_pipeline and asserts the
graph shape, the preserved disagreement, and the coverage summary. Rolls back on close.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_graph  # noqa: E402

pytestmark = pytest.mark.db


def _new_run(cur, source):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
        "VALUES (%s, 'test://t4', now(), now(), 'ok') RETURNING ingest_run_id",
        (source,),
    )
    return cur.fetchone()[0]


def _satcat(cur, run, norad, name, object_id, launch, ops, owner="US"):
    cur.execute(
        "INSERT INTO raw_satcat (norad_cat_id, object_name, object_id, object_type, "
        "ops_status_code, owner, launch_date, perigee, apogee, ingest_run_id) "
        "VALUES (%s,%s,%s,'PAYLOAD',%s,%s,%s,540,560,%s)",
        (norad, name, object_id, ops, owner, launch, run),
    )


def _gcat(cur, run, jcat, norad, piece, name, launch, status, owner=None):
    cur.execute(
        "INSERT INTO raw_gcat_satcat (jcat, norad_id, piece, object_type, name, pl_name, "
        "launch_date, status, owner, ingest_run_id) "
        "VALUES (%s,%s,%s,'P',%s,%s,%s,%s,%s,%s)",
        (jcat, norad, piece, name, name, launch, status, owner, run),
    )


def test_full_pipeline_on_synthetic_fixture(db_conn, tmp_path):
    review = tmp_path / "review.csv"
    with db_conn.cursor() as cur:
        srun = _new_run(cur, "satcat")
        # obj A/B/C: matched by NORAD. A carries a status disagreement (SATCAT + vs GCAT D).
        _satcat(cur, srun, 910000201, "STARLINK-201", "2023-101A", "2023-03-01", "+")
        _satcat(cur, srun, 910000202, "STARLINK-202", "2023-102A", "2023-03-01", "+")
        _satcat(cur, srun, 910000203, "STARLINK-203", "2023-103A", "2023-03-01", "+")
        # obj E: SATCAT-only
        _satcat(cur, srun, 910000205, "LONESAT", "2023-105A", "2023-03-05", "+")

        grun = _new_run(cur, "gcat")
        _gcat(cur, grun, "G201", 910000201, "2023-101A", "Starlink 201", "2023 Mar 1",
              "D", owner="SpaceX")  # DECAYED per GCAT while SATCAT says operational
        _gcat(cur, grun, "G202", 910000202, "2023-102A", "Starlink 202", "2023 Mar 1",
              "O", owner="SpaceX")
        _gcat(cur, grun, "G203", 910000203, "2023-103A", "Starlink 203", "2023 Mar 1",
              "O", owner="SpaceX")
        # obj D: GCAT-only analyst object, NORAD-less, standard COSPAR -> COSPAR pass
        _gcat(cur, grun, "G204", None, "2023-090Z", "Analyst Object", "2023 Feb 1", "O")

    summary = build_graph.run_pipeline(db_conn, review_csv=review)

    # exactly the 5 physical objects
    assert summary["satellites"] == 5

    ids = summary["identifiers_by_type"]
    assert ids.get("norad", 0) == 4          # 201, 202, 203, 205
    assert ids.get("gcat_id", 0) == 4        # G201-G204
    assert ids.get("name_satcat", 0) == 4

    assert summary["merge_log_rows"] > 0, "every link must be audited in merge_log"
    assert {"satcat", "gcat"} <= set(summary["assertions_by_source"])

    with db_conn.cursor() as cur:
        cur.execute("SELECT satellite_id FROM satellite WHERE norad_id = 910000201")
        obj_a = cur.fetchone()[0]
        # the SATCAT/GCAT status disagreement survives as data in source_assertion
        cur.execute(
            "SELECT count(DISTINCT value) FROM source_assertion "
            "WHERE satellite_id=%s AND attribute='status'",
            (obj_a,),
        )
        assert cur.fetchone()[0] == 2

        # the analyst object exists as its own satellite, keyed by COSPAR
        cur.execute(
            "SELECT count(*) FROM satellite_identifier "
            "WHERE id_type='cospar' AND id_value='2023-090Z'"
        )
        assert cur.fetchone()[0] >= 1

    # A/B/C resolve to SpaceX (GCAT owner + launch dates present); E/D do not
    assert summary["operator_resolved"] == 3
    assert summary["status_coverage_pct"] > 0
    db_conn.rollback()
