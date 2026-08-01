"""Tests for provisional-to-anchored promotion (identity/reconcile.py) and merge completeness.

The FK-completeness test is the important one: merge() deletes the merged satellite row last, so
any table that references satellite and is not repointed first turns that DELETE into a foreign
key violation. scripts/build_graph.py::run_pipeline commits once at the very end, so a single
violation would roll back an entire night's graph build, and deploy/nightly-refresh.sh would
swallow it into a log nobody reads. Two migrations (0007/0008 gold_case, 0009 satellite_bus) added
references after merge.py was written and neither updated it. This test fails on the next one.
"""

import pytest

from identity import merge as merge_mod
from identity.reconcile import name_gate

# Every table merge() repoints, read off the implementation. Kept as data so the test compares it
# against the live schema rather than against another copy of the same assumption.
_REPOINTED = {
    "satellite_identifier",
    "source_assertion",
    "satellite_status_history",
    "satellite_operator",
    "satellite_bus",
    "gold_case",
    "satellite_fcc_authorization",
    "satellite_manufacturer_credit",
}


@pytest.mark.db
def test_merge_repoints_every_table_that_references_satellite(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT tc.table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = 'satellite'
            """
        )
        referencing = {r[0] for r in cur.fetchall()}
    missing = referencing - _REPOINTED
    assert not missing, (
        f"tables reference satellite but merge() does not repoint them: {sorted(missing)}. "
        "Add them to identity/merge.py::merge and to _REPOINTED here."
    )
    source = (merge_mod.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as fh:
        body = fh.read()
    for table in referencing:
        assert f"UPDATE {table} SET satellite_id" in body, f"merge() must repoint {table}"


def test_name_gate_accepts_catalog_placeholders():
    """Space-Track's placeholder names re-encode the volatile piece letter and carry no signal."""
    assert name_gate("MISR-D-1", "TRANSPORTER-17 OBJECT AJ")
    assert name_gate("Nova 1", "TRANSPORTER-17 OBJECT BH")


def test_name_gate_accepts_normalized_agreement():
    assert name_gate("STARLINK-30042", "Starlink 30042")
    assert name_gate("ICEYE-X41", "iceye x41")


def test_name_gate_declines_real_disagreement():
    """A genuine name conflict is a promotion we must not make automatically."""
    assert not name_gate("Flashpoint 1", "GRUS-3A")
    assert not name_gate("Nova 1", "Posidonia")


def test_name_gate_declines_when_provisional_name_is_empty():
    """An empty name normalizes to '' and must not match another empty name into a merge."""
    assert not name_gate(None, "Kostka")
    assert not name_gate("", "")


@pytest.mark.db
def test_promotion_leaves_no_provisional_duplicate_of_an_anchored_satellite(db_conn):
    """After the nightly build, no COSPAR should resolve to both a provisional and an anchored
    record with the same launch date. That state is exactly what makes downstream joins pick
    whichever record happened to carry the key they matched on."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM satellite p
            JOIN satellite_identifier pi
              ON pi.satellite_id = p.satellite_id AND pi.id_type = 'cospar' AND pi.valid_to IS NULL
            JOIN satellite_identifier ni
              ON ni.id_type = 'cospar' AND ni.id_value = pi.id_value AND ni.valid_to IS NULL
            JOIN satellite n ON n.satellite_id = ni.satellite_id
            WHERE p.norad_id IS NULL AND n.norad_id IS NOT NULL
              AND p.satellite_id <> n.satellite_id
              AND p.launch_date IS NOT DISTINCT FROM n.launch_date
            """
        )
        unpromoted = cur.fetchone()[0]
    assert unpromoted == 0, (
        f"{unpromoted} provisional records still shadow an anchored satellite; "
        "run scripts/build_graph.py to promote them"
    )
