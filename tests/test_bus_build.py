"""Tests for the satellite_bus attribution build (identity/bus.py + migration 0009).

DB-backed checks run against the shared dev database's real build output (scripts/build_bus.py
is part of the daily cycle, so satellite_bus is expected to be populated); the normalization
rules are asserted on the stored rows themselves, which makes these tests double as data-quality
gates on the live attribution.
"""

import pytest

from identity import bus


@pytest.mark.db
def test_attribution_populated_with_provenance(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(bus_model), count(manufacturer_code) FROM satellite_bus"
        )
        total, with_bus, with_manufacturer = cur.fetchone()
    assert total > 10000, "the GCAT payload catalog attributes tens of thousands of buses"
    assert with_bus > 0 and with_manufacturer > 0

    with db_conn.cursor() as cur:
        # Row provenance is NOT NULL by schema; verify the assertion-layer provenance exists too.
        cur.execute(
            "SELECT count(*) FROM source_assertion "
            "WHERE source = 'gcat' AND attribute IN ('bus', 'manufacturer')"
        )
        assertions = cur.fetchone()[0]
    assert assertions > 0, "bus/manufacturer claims must be extracted into source_assertion"


@pytest.mark.db
def test_bus_normalization_rules_hold(db_conn):
    with db_conn.cursor() as cur:
        # The '?' uncertainty marker never leaks into the normalized model name...
        cur.execute("SELECT count(*) FROM satellite_bus WHERE bus_model LIKE '%?'")
        assert cur.fetchone()[0] == 0
        # ...but uncertain attributions are flagged, not dropped.
        cur.execute(
            "SELECT count(*) FROM satellite_bus WHERE bus_uncertain AND bus_raw LIKE '%?'"
        )
        assert cur.fetchone()[0] > 0
        # Casing variants collapse: one slug never maps to two display spellings.
        cur.execute(
            "SELECT count(*) FROM (SELECT bus_slug FROM satellite_bus WHERE bus_slug IS NOT NULL "
            "GROUP BY bus_slug HAVING count(DISTINCT bus_model) > 1) t"
        )
        assert cur.fetchone()[0] == 0
        # Placeholder values (UNK etc.) are dropped rather than benchmarked as models.
        cur.execute("SELECT count(*) FROM satellite_bus WHERE lower(bus_model) = 'unk'")
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_manufacturer_rollup_rules_hold(db_conn):
    with db_conn.cursor() as cur:
        # The curated SPXS -> SPX override is visible and flagged as an override.
        cur.execute(
            "SELECT DISTINCT manufacturer_group_code, rollup_source FROM satellite_bus "
            "WHERE manufacturer_code = 'SPXS'"
        )
        rows = cur.fetchall()
    assert rows == [("SPX", "gcat_orgs+override")]

    with db_conn.cursor() as cur:
        # Business-class rollup only: no satellite rolls up into the Soviet ministry MOM or the
        # Roskosmos agency FKA (state orgs are not manufacturers here).
        cur.execute(
            "SELECT count(*) FROM satellite_bus WHERE manufacturer_group_code IN ('MOM', 'FKA') "
            "AND manufacturer_code NOT IN ('MOM', 'FKA')"
        )
        assert cur.fetchone()[0] == 0
        # Rollup provenance: every rolled-up row records its traversal path from leaf to group.
        cur.execute(
            "SELECT count(*) FROM satellite_bus WHERE rollup_source LIKE 'gcat_orgs%' AND ("
            "rollup_path IS NULL OR rollup_path[1] <> manufacturer_code "
            "OR rollup_path[array_length(rollup_path, 1)] <> manufacturer_group_code)"
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_build_is_idempotent_full_rebuild(db_conn):
    """Re-running the build inside a rolled-back transaction reproduces the same row count."""
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM satellite_bus")
        before = cur.fetchone()[0]
    try:
        stats = bus.build(db_conn)
        assert stats["attributed"] == before
        assert stats["with_manufacturer"] > 0
    finally:
        db_conn.rollback()


@pytest.mark.db
def test_snapshot_capture_is_idempotent_within_month(db_conn):
    """Second capture in the same month inserts nothing: the monthly record is immutable."""
    try:
        first = bus.snapshot_benchmarks(db_conn)
        if first["manufacturer"] is None:
            pytest.skip("benchmark views not applied in this database")
        second = bus.snapshot_benchmarks(db_conn)
        assert second["manufacturer"] == 0 and second["bus"] == 0
    finally:
        db_conn.rollback()
