"""Operator-enrichment tests: seed-wins dedup, enriched creation + class mapping, non-ASCII display
name, name-collision disambiguation, GCAT parent -> subsidiary_of relationships, SATCAT code
attachment, and idempotent re-runs.

Isolation: every DB write happens on the test connection WITHOUT committing and is rolled back at
the end. Synthetic GCAT/SATCAT snapshots are inserted under fresh `ingest_run` rows, which become the
latest OK runs on this connection, so `enrich()` operates only on the synthetic owner codes (NORAD
range 930000001-939999999). Enriched-operator lookups filter by the synthetic code set, so a
populated live operator table never leaks in.
"""

import pytest

from identity import enrich_operators

pytestmark = pytest.mark.db

SEED_OP = "OEI Zeta Seed Test"

# code -> (name, ename, class, parent, t_start)
_ORGS = {
    "ZSN": ("OEI Zeta Seed Test (Kanata)", None, "B", None, None),   # seed-wins by org name
    "ZSC": ("OEI Zeta Different Label", None, "B", None, None),      # seed-wins by seed gcat_code
    "ZEA": ("OEI Enrich Alpha", None, "B", None, None),             # enriched (commercial)
    "ZEB": ("OEI Enrich Beta", None, "D", "ZEA", "2015"),           # enriched (defense), child of ZEA
    "ZD1": ("OEI Dup Label", None, "C", None, None),                # collision pair (civil)
    "ZD2": ("OEI Dup Label", None, "C", None, None),                # -> "OEI Dup Label (ZD2)"
    "ZNA": ("Тест Организация", "OEI NonAscii EN", "A", None, None),  # non-ASCII -> EName (academic)
}
_SATCAT_CODES = {"ZSCAT": SEED_OP, "ZCNTRY": "OEI Zedland"}


def _new_run(cur, source, endpoint) -> int:
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, status, started_at, finished_at) "
        "VALUES (%s, %s, 'ok', now(), now()) RETURNING ingest_run_id",
        (source, endpoint),
    )
    return cur.fetchone()[0]


@pytest.fixture
def synthetic(db_conn, tmp_path):
    """Insert synthetic seed operator + GCAT/SATCAT snapshots (uncommitted); yield paths; rollback."""
    cur = db_conn.cursor()
    cur.execute(
        "INSERT INTO operator (canonical_name, country, operator_class) "
        "VALUES (%s, 'US', 'commercial') RETURNING operator_id",
        (SEED_OP,),
    )
    seed_op_id = cur.fetchone()[0]

    orgs_run = _new_run(cur, "gcat", "orgs_enrich_test")
    for code, (name, ename, cls, parent, tstart) in _ORGS.items():
        cur.execute(
            "INSERT INTO raw_gcat_orgs (code, state_code, org_type, org_class, t_start, "
            "short_name, name, e_name, parent_code, ingest_run_id) "
            "VALUES (%s, %s, 'O', %s, %s, %s, %s, %s, %s, %s)",
            (code, "RU" if cls == "D" else "US", cls, tstart, code, name, ename, parent, orgs_run),
        )

    gsat_run = _new_run(cur, "gcat", "satcat_enrich_test")
    for i, code in enumerate(_ORGS, start=1):
        cur.execute(
            "INSERT INTO raw_gcat_satcat (jcat, norad_id, owner, ingest_run_id) "
            "VALUES (%s, %s, %s, %s)",
            (f"ZJC{i}", 930000000 + i, code, gsat_run),
        )

    sat_run = _new_run(cur, "celestrak", "satcat_enrich_test")
    for i, code in enumerate(_SATCAT_CODES, start=1):
        cur.execute(
            "INSERT INTO raw_satcat (norad_cat_id, owner, ingest_run_id) VALUES (%s, %s, %s)",
            (930000020 + i, code, sat_run),
        )

    seed_yml = tmp_path / "seed.yml"
    seed_yml.write_text(
        "operators:\n"
        f"  - name: {SEED_OP}\n"
        "    country: US\n"
        "    class: commercial\n"
        "    aliases: [OEI Zeta Seed]\n"
        "    satcat_codes: [ZSCAT]\n"
        "    gcat_codes: [ZSC]\n"
        "relationships: []\n"
    )
    satcat_yml = tmp_path / "satcat_codes.yml"
    satcat_yml.write_text(
        "codes:\n" + "".join(f'  {c}: "{n}"\n' for c, n in _SATCAT_CODES.items())
    )

    try:
        yield db_conn, seed_op_id, str(seed_yml), str(satcat_yml)
    finally:
        db_conn.rollback()


def _op_id(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT operator_id FROM operator WHERE canonical_name = %s", (name,))
        row = cur.fetchone()
        return row[0] if row else None


def _alias_op(conn, alias, source):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT operator_id FROM operator_alias WHERE alias = %s AND source = %s",
            (alias, source),
        )
        rows = cur.fetchall()
        return rows[0][0] if len(rows) == 1 else (None if not rows else "MULTIPLE")


def test_seed_wins_by_org_name_and_by_seed_code(synthetic):
    conn, seed_op_id, seed_yml, satcat_yml = synthetic
    enrich_operators.enrich(conn, operator_seed_path=seed_yml, satcat_codes_path=satcat_yml)

    # No duplicate operator was spawned for the seed-owned orgs...
    assert _op_id(conn, "OEI Zeta Seed Test (Kanata)") is None
    assert _op_id(conn, "OEI Zeta Different Label") is None
    # ...instead both codes attach as aliases to the existing seed operator.
    assert _alias_op(conn, "ZSN", "gcat_orgs") == seed_op_id  # matched by normalized org name
    assert _alias_op(conn, "ZSC", "gcat_orgs") == seed_op_id  # matched by seed gcat_code


def test_enriched_operators_created_with_class_and_country(synthetic):
    conn, _seed, seed_yml, satcat_yml = synthetic
    enrich_operators.enrich(conn, operator_seed_path=seed_yml, satcat_codes_path=satcat_yml)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT operator_class, country FROM operator WHERE canonical_name = 'OEI Enrich Alpha'"
        )
        assert cur.fetchone() == ("commercial", "US")  # class B -> commercial
        cur.execute(
            "SELECT operator_class, country FROM operator WHERE canonical_name = 'OEI Enrich Beta'"
        )
        assert cur.fetchone() == ("defense", "RU")  # class D -> defense
    assert _alias_op(conn, "ZEA", "gcat_orgs") == _op_id(conn, "OEI Enrich Alpha")


def test_non_ascii_name_uses_ename_and_maps_academic(synthetic):
    conn, _seed, seed_yml, satcat_yml = synthetic
    enrich_operators.enrich(conn, operator_seed_path=seed_yml, satcat_codes_path=satcat_yml)
    op_id = _op_id(conn, "OEI NonAscii EN")  # EName wins because Name is non-ASCII
    assert op_id is not None
    assert _alias_op(conn, "ZNA", "gcat_orgs") == op_id
    with conn.cursor() as cur:
        cur.execute("SELECT operator_class FROM operator WHERE operator_id = %s", (op_id,))
        assert cur.fetchone()[0] == "academic"  # class A -> academic


def test_canonical_name_collision_is_disambiguated_not_crashed(synthetic):
    conn, _seed, seed_yml, satcat_yml = synthetic
    enrich_operators.enrich(conn, operator_seed_path=seed_yml, satcat_codes_path=satcat_yml)
    # Two different codes share the Name "OEI Dup Label" -> the second disambiguates on its code.
    assert _op_id(conn, "OEI Dup Label") is not None
    assert _op_id(conn, "OEI Dup Label (ZD2)") is not None
    assert _op_id(conn, "OEI Dup Label") != _op_id(conn, "OEI Dup Label (ZD2)")
    assert _alias_op(conn, "ZD2", "gcat_orgs") == _op_id(conn, "OEI Dup Label (ZD2)")


def test_gcat_parent_becomes_subsidiary_relationship(synthetic):
    conn, _seed, seed_yml, satcat_yml = synthetic
    enrich_operators.enrich(conn, operator_seed_path=seed_yml, satcat_codes_path=satcat_yml)
    child = _op_id(conn, "OEI Enrich Beta")
    parent = _op_id(conn, "OEI Enrich Alpha")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT valid_from, source FROM operator_relationship "
            "WHERE child_id = %s AND parent_id = %s AND relationship = 'subsidiary_of'",
            (child, parent),
        )
        row = cur.fetchone()
    assert row is not None
    assert str(row[0]) == "2015-01-01"  # t_start parsed
    assert row[1] == "gcat_orgs"


def test_satcat_codes_attach_to_seed_and_create_country_operator(synthetic):
    conn, seed_op_id, seed_yml, satcat_yml = synthetic
    enrich_operators.enrich(conn, operator_seed_path=seed_yml, satcat_codes_path=satcat_yml)
    # ZSCAT resolves to the seed operator (seed satcat_code); ZCNTRY has no match -> new operator.
    assert _alias_op(conn, "ZSCAT", "satcat_sources") == seed_op_id
    zedland = _op_id(conn, "OEI Zedland")
    assert zedland is not None
    assert _alias_op(conn, "ZCNTRY", "satcat_sources") == zedland


def test_enrichment_is_idempotent(synthetic):
    conn, _seed, seed_yml, satcat_yml = synthetic
    first = enrich_operators.enrich(conn, operator_seed_path=seed_yml, satcat_codes_path=satcat_yml)

    def _counts():
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM operator")
            ops = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM operator_alias WHERE source IN ('gcat_orgs','satcat_sources')")
            aliases = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM operator_relationship WHERE source = 'gcat_orgs'")
            rels = cur.fetchone()[0]
        return ops, aliases, rels

    before = _counts()
    second = enrich_operators.enrich(conn, operator_seed_path=seed_yml, satcat_codes_path=satcat_yml)
    after = _counts()

    assert before == after  # no new operators/aliases/relationships on a re-run
    # Second pass creates nothing new (all GCAT codes short-circuit via their code alias).
    assert second["gcat_operators_created"] == 0
    assert second["satcat_operators_created"] == 0
    # Every synthetic code alias points at exactly one operator (no duplicate/ambiguous aliases).
    for code in _ORGS:
        assert _alias_op(conn, code, "gcat_orgs") not in (None, "MULTIPLE")
    # Sanity: the first pass actually did the work it's now not repeating.
    assert first["gcat_operators_created"] >= 3
