"""Slug stability and operator-merge gates (Phase 4, docs/design/0002-phase4-brief.md).

A manufacturer slug is a public URL (/buses/{slug}) and the primary key of the frozen monthly
archive, so an attribution change is never only a code change: it can break links, split
historical series, or silently rewrite published numbers. These tests pin the exact measured
outcome of the operator-identity merge and the invariants that keep the URL contract intact.
Every expected value was measured against the live build before being pinned; a failure here
means published surfaces moved in a way nobody decided.
"""

import pathlib

import pytest

from identity import bus as bus_mod

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The one merge this phase ships: the Planet family. Everything else must be byte-stable.
EXPECTED_ALIASES = {("plabs", "plan"), ("cosmog", "plan"), ("skybox", "plan")}


@pytest.mark.db
def test_alias_table_holds_exactly_the_planet_merge(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT old_slug, new_slug FROM benchmark_slug_alias WHERE kind = 'manufacturer'"
        )
        assert set(cur.fetchall()) == EXPECTED_ALIASES
        cur.execute("SELECT count(*) FROM benchmark_slug_alias WHERE kind <> 'manufacturer'")
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_every_frozen_manufacturer_slug_still_resolves(db_conn):
    """The frozen archive must never orphan: every snapshotted slug resolves to a live cohort
    directly or through exactly one alias hop."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE live.manufacturer_slug IS NULL
                                      AND via.manufacturer_slug IS NULL) AS unresolvable,
                   count(*) FILTER (WHERE live.manufacturer_slug IS NULL
                                      AND via.manufacturer_slug IS NOT NULL) AS via_alias
            FROM (SELECT DISTINCT slug FROM bus_benchmark_snapshots
                  WHERE kind = 'manufacturer') f
            LEFT JOIN v_bus_benchmarks_manufacturer live ON live.manufacturer_slug = f.slug
            LEFT JOIN benchmark_slug_alias a
                   ON a.kind = 'manufacturer' AND a.old_slug = f.slug
            LEFT JOIN v_bus_benchmarks_manufacturer via ON via.manufacturer_slug = a.new_slug
            """
        )
        unresolvable, via_alias = cur.fetchone()
    assert unresolvable == 0, "a frozen series lost its cohort with no redirect"
    assert via_alias == 3


@pytest.mark.db
def test_frozen_bus_rows_embedded_manufacturer_slugs_resolve(db_conn):
    """The second frozen surface: bus-kind snapshot rows embed a primary_manufacturer_slug in
    their metrics blob, and a manufacturer merge can strand those without any bus slug moving."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE live.manufacturer_slug IS NULL
                                      AND via.manufacturer_slug IS NULL)
            FROM (SELECT DISTINCT metrics->>'primary_manufacturer_slug' AS ps
                  FROM bus_benchmark_snapshots WHERE kind = 'bus') f
            LEFT JOIN v_bus_benchmarks_manufacturer live ON live.manufacturer_slug = f.ps
            LEFT JOIN benchmark_slug_alias a ON a.kind = 'manufacturer' AND a.old_slug = f.ps
            LEFT JOIN v_bus_benchmarks_manufacturer via ON via.manufacturer_slug = a.new_slug
            WHERE f.ps IS NOT NULL
            """
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_planet_merge_outcome(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT manufacturer_name, manufacturer_group_code, fleet_total, org_count "
            "FROM v_bus_benchmarks_manufacturer WHERE manufacturer_slug = 'plan'"
        )
        name, gcode, fleet, orgs = cur.fetchone()
    assert (name, gcode, fleet) == ("Planet", "PLAN", 661)
    assert orgs == 4, "PLAN + PLABS + COSMOG + SKYBOX leaf codes"


@pytest.mark.db
def test_retired_slugs_return_no_view_row(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM v_bus_benchmarks_manufacturer "
            "WHERE manufacturer_slug IN ('plabs', 'cosmog', 'skybox')"
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_representative_is_fleet_max_not_alphabetical(db_conn):
    """The surviving slug is a published-URL decision: fleet-max keeps /buses/plan, where an
    alphabetical ORDER BY would hand the 661-satellite cohort to /buses/cosmog (fleet 2). This
    test is what stops a later ORDER BY edit from relocating a published URL."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT manufacturer_group_code FROM satellite_bus "
            "WHERE manufacturer_slug = 'plan'"
        )
        assert cur.fetchall() == [("PLAN",)]
    assert "c.fleet DESC, c.gcode ASC" in bus_mod._COHORTS_CTE


@pytest.mark.db
def test_merge_only_no_splits(db_conn):
    """Structural guarantee: keying the merge on the group code can join cohorts but never split
    one. Every group code maps to exactly one slug."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM (SELECT manufacturer_group_code FROM satellite_bus "
            "WHERE manufacturer_group_code IS NOT NULL "
            "GROUP BY 1 HAVING count(DISTINCT manufacturer_slug) > 1) z"
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_view_cardinality_one_row_per_slug(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(DISTINCT manufacturer_slug) FROM v_bus_benchmarks_manufacturer"
        )
        total, distinct = cur.fetchone()
        assert total == distinct
        cur.execute("SELECT count(*), count(DISTINCT bus_slug) FROM v_bus_benchmarks_bus")
        btotal, bdistinct = cur.fetchone()
        assert btotal == bdistinct


@pytest.mark.db
def test_alias_source_restriction_blocks_country_codes(db_conn):
    """GCAT org codes and SATCAT country codes share a namespace: without the source restriction
    POL (Polyot, 95 satellites) resolves to Poland, unambiguously and wrongly.

    Pins the RULE, not the outcome: an earlier version asserted these codes never resolve at
    all, which broke honestly in 2026-08 when the refreshed org registry added a legitimate
    in-namespace alias (COL -> Columbia University, source gcat_orgs). What must hold forever
    is that any resolution rides an alias from the restricted sources, never the satcat
    country-code namespace."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT manufacturer_group_code, manufacturer_group_operator_id "
            "FROM satellite_bus "
            "WHERE manufacturer_group_code IN ('POL', 'COL', 'LTU') "
            "  AND manufacturer_group_operator_id IS NOT NULL"
        )
        for gcode, opid in cur.fetchall():
            cur.execute(
                "SELECT 1 FROM operator_alias WHERE alias = %s AND operator_id = %s "
                "AND source IN ('gcat_orgs', 'gcat', 'seed')",
                (gcode, opid),
            )
            assert cur.fetchone() is not None, (
                f"{gcode} resolved to operator {opid} without an in-namespace alias: "
                "the country-code restriction has been bypassed"
            )
        # The concrete country hazard, forever: Polyot's cohort never becomes Poland's.
        cur.execute(
            "SELECT manufacturer_name, fleet_total FROM v_bus_benchmarks_manufacturer "
            "WHERE manufacturer_slug = 'pol'"
        )
        assert cur.fetchone() == ("Polyot", 95)


@pytest.mark.db
def test_no_ambiguous_alias_is_used_as_a_group_code(db_conn):
    """Zero today, pinned so it stays zero: an ambiguous alias entering the merge path would make
    cohort membership depend on the tiebreak instead of on curation."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            WITH ambiguous AS (
                SELECT alias FROM operator_alias
                WHERE source IN ('gcat_orgs', 'gcat', 'seed')
                GROUP BY alias HAVING count(DISTINCT operator_id) > 1
            )
            SELECT count(*) FROM ambiguous a
            JOIN (SELECT DISTINCT manufacturer_group_code g FROM satellite_bus) u ON u.g = a.alias
            """
        )
        assert cur.fetchone()[0] == 0


def test_no_operator_relationship_traversal():
    """The rollup is undated, current-state attribution by design (no build date exists in any
    catalog), and the operator graph's parent edges are founding dates, not M&A dates. The
    manufacturer build must never traverse them."""
    source = pathlib.Path(bus_mod.__file__).read_text(encoding="utf-8")
    assert "operator_relationship" not in source


@pytest.mark.db
def test_ungated_walk_tripwires(db_conn):
    """Outcome pins for the cohorts an operator_relationship walk would silently rewrite
    (NASA field centres into NASA, design bureaus into Roskosmos, SAST splitting 188 to 1)."""
    expected = {"gsfc": 66, "jpl": 50, "sast": 188, "resh": 156, "cast": 414, "nrl": 100}
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT manufacturer_slug, fleet_total FROM v_bus_benchmarks_manufacturer "
            "WHERE manufacturer_slug = ANY(%s)",
            (list(expected),),
        )
        got = dict(cur.fetchall())
    assert got == expected


@pytest.mark.db
def test_unresolved_group_codes_keep_incumbent_slug(db_conn):
    """The fallback for the ~317 group codes the operator graph does not know is structural:
    no operator match means no rewrite, so the slug is still the slugified group code."""
    with db_conn.cursor() as cur:
        cur.execute(
            r"""
            SELECT count(*) FROM satellite_bus
            WHERE manufacturer_group_operator_id IS NULL
              AND manufacturer_group_code IS NOT NULL
              AND manufacturer_slug IS DISTINCT FROM
                  NULLIF(btrim(regexp_replace(lower(manufacturer_group_code),
                                              '[^a-z0-9]+', '-', 'g'), '-'), '')
            """
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM satellite_bus")
        assert cur.fetchone()[0] > 27000


@pytest.mark.db
def test_sum_identity_holds(db_conn):
    """A reader summing the leaderboard must land exactly on the attributed satellite count:
    merge-only rewrites move satellites between cohorts but never mint or double-count one."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT sum(fleet_total) FROM v_bus_benchmarks_manufacturer), "
            "(SELECT count(*) FROM satellite_bus WHERE manufacturer_slug IS NOT NULL)"
        )
        summed, counted = cur.fetchone()
        assert int(summed) == counted


@pytest.mark.db
def test_cross_kind_collision_set_is_stable(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM (SELECT manufacturer_slug FROM v_bus_benchmarks_manufacturer "
            "INTERSECT SELECT bus_slug FROM v_bus_benchmarks_bus) z"
        )
        assert cur.fetchone()[0] == 29, (
            "a new cross-kind collision is a 200 serving the wrong entity, worse than a 404"
        )


@pytest.mark.db
def test_snapshot_month_frozen_once(db_conn):
    """With the current month already captured, another snapshot run inserts nothing at all,
    including for cohorts that did not exist at first freeze."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM bus_benchmark_snapshots "
            "WHERE snapshot_month = date_trunc('month', current_date)::date"
        )
        already = cur.fetchone()[0]
    if already == 0:
        pytest.skip("current month not captured yet in this database")
    inserted = bus_mod.snapshot_benchmarks(db_conn)
    assert inserted == {"manufacturer": 0, "bus": 0}
    db_conn.rollback()


@pytest.mark.db
def test_retired_slug_still_serves_via_api(db_conn):
    """The URL contract end to end: /buses/plabs serves the surviving Planet cohort and says so."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient

    from api.main import app

    client = TestClient(app)
    r = client.get("/api/buses/plabs")
    assert r.status_code == 200
    body = r.json()
    assert body["aliased_from"] == "plabs"
    assert body["benchmark"]["slug"] == "plan"
    assert body["benchmark"]["fleet_total"] == 661
    # Receipts reconcile against the surviving cohort's headline.
    r2 = client.get("/api/buses/plabs/provenance?metric=fleet")
    assert r2.status_code == 200
    assert r2.json()["total"] == 661
    # The frozen series is reachable and names its continuation.
    r3 = client.get("/api/buses/history/plabs")
    assert r3.status_code == 200
    assert r3.json()["continued_as"] == "plan"
    # A live slug is never shadowed by the alias path.
    r4 = client.get("/api/buses/plan")
    assert r4.status_code == 200
    assert r4.json()["aliased_from"] is None


@pytest.mark.db
def test_methodology_version_matches_changelog():
    assert bus_mod.METHODOLOGY_VERSION == "1.7"
    doc = (REPO_ROOT / "docs" / "BUS_BENCHMARKS_METHODOLOGY.md").read_text(encoding="utf-8")
    top = doc.split("## Changelog")[1].strip().splitlines()[0]
    assert "v1.7" in top
    assert "Planet" in doc.split("## Changelog")[1]
