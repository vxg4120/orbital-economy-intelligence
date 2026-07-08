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

    # Global summary fields that stay valid on a shared/populated dev DB (the identity build is
    # committed there); the per-object graph shape is asserted synthetic-scoped below so this test
    # is robust to the reserved-range convention rather than assuming an empty DB.
    assert summary["merge_log_rows"] > 0, "every link must be audited in merge_log"
    assert {"satcat", "gcat"} <= set(summary["assertions_by_source"])

    with db_conn.cursor() as cur:
        # exactly the 5 synthetic physical objects: 4 by NORAD (201/202/203/205) + 1 COSPAR-only
        # analyst (G204 -> 2023-090Z).
        cur.execute(
            "SELECT count(DISTINCT s.satellite_id) FROM satellite s "
            "LEFT JOIN satellite_identifier si ON si.satellite_id = s.satellite_id "
            "WHERE s.norad_id BETWEEN 910000201 AND 910000205 "
            "   OR (si.id_type='cospar' AND si.id_value='2023-090Z')"
        )
        assert cur.fetchone()[0] == 5

        cur.execute(
            "SELECT count(*) FROM satellite_identifier "
            "WHERE id_type='norad' AND id_value = ANY(%s)",
            (["910000201", "910000202", "910000203", "910000205"],),
        )
        assert cur.fetchone()[0] == 4
        cur.execute(
            "SELECT count(*) FROM satellite_identifier "
            "WHERE id_type='gcat_id' AND id_value = ANY(%s)",
            (["G201", "G202", "G203", "G204"],),
        )
        assert cur.fetchone()[0] == 4  # G201-G204 all linked (G204 via COSPAR)

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

        # A/B/C resolve to SpaceX (GCAT owner + launch dates present); each gets one owner row.
        cur.execute(
            "SELECT DISTINCT o.canonical_name FROM satellite_operator so "
            "JOIN operator o ON o.operator_id = so.operator_id "
            "JOIN satellite s ON s.satellite_id = so.satellite_id "
            "WHERE s.norad_id IN (910000201, 910000202, 910000203) "
            "  AND so.role='owner' AND so.source='resolve'"
        )
        resolved_ops = [r[0] for r in cur.fetchall()]
        assert resolved_ops == ["SpaceX"], resolved_ops
        cur.execute(
            "SELECT count(*) FROM satellite_operator so JOIN satellite s "
            "ON s.satellite_id = so.satellite_id "
            "WHERE s.norad_id IN (910000201, 910000202, 910000203) "
            "  AND so.role='owner' AND so.source='resolve'"
        )
        assert cur.fetchone()[0] == 3  # E (SATCAT-only, no GCAT owner) and D (analyst) do not

    assert summary["status_coverage_pct"] > 0
    db_conn.rollback()
