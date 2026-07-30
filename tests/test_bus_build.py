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
        # Outcome pins rather than provenance strings: the earlier assertion pinned
        # rows == [("SPX", "gcat_orgs+override")], a string that legitimately changes under
        # refactors, which would get edited away and leave 12,685 satellites unguarded. What must
        # never change silently is the OUTCOME: SPXS lands on group SPX, and /buses/spx keeps its
        # full fleet.
        cur.execute(
            "SELECT DISTINCT manufacturer_group_code FROM satellite_bus "
            "WHERE manufacturer_code = 'SPXS'"
        )
        assert cur.fetchall() == [("SPX",)]
        cur.execute("SELECT count(*) FROM satellite_bus WHERE manufacturer_slug = 'spx'")
        assert cur.fetchone()[0] == 12829, "the site's largest page must keep its fleet"
        cur.execute(
            "SELECT count(*) FROM satellite_bus WHERE rollup_source = 'gcat_orgs+override'"
        )
        assert cur.fetchone()[0] == 12685, "the SPXS override must still fire on every row"

    with db_conn.cursor() as cur:
        # Business-class rollup only: no satellite rolls up into the Soviet ministry MOM or the
        # Roskosmos agency FKA (state orgs are not manufacturers here).
        cur.execute(
            "SELECT count(*) FROM satellite_bus WHERE manufacturer_group_code IN ('MOM', 'FKA') "
            "AND manufacturer_code NOT IN ('MOM', 'FKA')"
        )
        assert cur.fetchone()[0] == 0
        # Rollup provenance: every GCAT-walked row records its traversal path from leaf to group.
        # Rows the operator merge rewrote carry rollup_source='operator_merge' and keep the path
        # of their original walk, so they are excluded here; the anti-vacuity floor guarantees
        # this filter still covers the walked majority rather than passing on zero rows.
        cur.execute(
            "SELECT count(*) FROM satellite_bus WHERE rollup_source LIKE 'gcat_orgs%'"
        )
        assert cur.fetchone()[0] > 10000, "walked-rollup filter must not go vacuous"
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


@pytest.mark.db
def test_attribution_agrees_with_piece_crosswalk(db_conn):
    """Every attributed row must sit on the satellite its COSPAR piece points to.

    GCAT reshuffles provisional jcat slots between releases on fresh multi-payload launches
    (observed on the 2026-07-07 Transporter-17 rideshare, where jcat-only matching dropped one
    Apex satellite and mis-joined three others), so attribution matches piece-first. This is
    the invariant that reshuffle violated.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (SELECT max(r.ingest_run_id) run FROM raw_gcat_satcat r
                            JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
                            WHERE i.status = 'ok')
            SELECT count(*)
            FROM satellite_bus sb
            JOIN raw_gcat_satcat rc ON rc.jcat = sb.source_key
            JOIN latest ON rc.ingest_run_id = latest.run
            WHERE EXISTS (SELECT 1 FROM satellite_identifier si
                          WHERE si.id_type = 'cospar' AND si.source = 'gcat'
                            AND si.id_value = btrim(rc.piece))
              AND NOT EXISTS (SELECT 1 FROM satellite_identifier si
                              WHERE si.id_type = 'cospar' AND si.source = 'gcat'
                                AND si.id_value = btrim(rc.piece)
                                AND si.satellite_id = sb.satellite_id)
            """
        )
        disagreements = cur.fetchone()[0]
    assert disagreements == 0


@pytest.mark.db
def test_apex_fleet_fully_attributed(db_conn):
    """Known-data regression for the jcat-reshuffle bug: GCAT credits Apex Space with five
    spacecraft (Aries 1 plus four Transporter-17 payloads); jcat-only matching surfaced four."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT fleet_total FROM v_bus_benchmarks_manufacturer WHERE manufacturer_slug='apex'"
        )
        row = cur.fetchone()
    assert row is not None and row[0] >= 5


@pytest.mark.db
def test_manufacturer_slug_is_unique(db_conn):
    """A slug is a public URL (/buses/{slug}) and the primary key of bus_benchmark_snapshots.

    Two cohorts sharing one slug means an ambiguous page and a frozen monthly series that can
    only ever capture one of them, silently dropping the other's history.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT manufacturer_slug, count(*) FROM v_bus_benchmarks_manufacturer "
            "WHERE manufacturer_slug IS NOT NULL GROUP BY 1 HAVING count(*) > 1"
        )
        collisions = cur.fetchall()
        cur.execute(
            "SELECT bus_slug, count(*) FROM v_bus_benchmarks_bus "
            "WHERE bus_slug IS NOT NULL GROUP BY 1 HAVING count(*) > 1"
        )
        collisions += cur.fetchall()
    assert not collisions, f"slugs must be unique, found collisions: {collisions}"


@pytest.mark.db
def test_uncertainty_marker_never_survives_into_a_resolved_code(db_conn):
    """GCAT marks an uncertain org with '?', and on a joint build it marks the individual code
    ('RAYM?/GSFC') rather than the end of the string. A marker left inside a resolved code
    matches no org and then slugifies onto the certain org it was meant to qualify."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM satellite_bus "
            "WHERE manufacturer_code LIKE '%?%' OR manufacturer_group_code LIKE '%?%' "
            "   OR array_to_string(manufacturer_codes, ',') LIKE '%?%'"
        )
        leaked = cur.fetchone()[0]
    assert leaked == 0, f"{leaked} rows carry an uncertainty marker inside a resolved org code"


@pytest.mark.db
def test_uncertainty_flag_covers_markers_anywhere_in_the_string(db_conn):
    """manufacturer_uncertain must be true whenever GCAT expressed any doubt, including on a
    non-final code of a compound build."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM satellite_bus "
            "WHERE manufacturer_raw LIKE '%?%' AND NOT manufacturer_uncertain"
        )
        missed = cur.fetchone()[0]
    assert missed == 0, f"{missed} rows carry '?' in the raw value but are not flagged uncertain"
