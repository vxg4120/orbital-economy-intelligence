"""The curated alias contract (identity/manufacturer_aliases.yml, methodology v1.9).

A fixed list of known aliases must resolve to one group each, so the 2026-09-16 audit's split
cohorts (NPO PM under three codes, Lockheed under two, the Loral 1300 bus under three names)
can never come back unnoticed, and a typo or truncation GCAT carries in a short name never
reaches a published cohort name again. The network-free tests pin the table itself; the
db-marked ones pin the build's outcome on the live attribution.
"""

import pytest

from identity import bus

KNOWN_MANUFACTURER_ALIASES = {"NPOPMR": "NPOPM", "RESH": "NPOPM", "LAC": "LM"}
KNOWN_DISPLAY_NAMES = {
    "NPOPM": "ISS Reshetnev (NPO PM)",
    "LM": "Lockheed Martin",
    "ONEWUS": "OneWeb",
    "ISAC": "ISRO Satellite Centre, Bangalore",
    "RESH": "ISS Reshetnev",
}
KNOWN_BUS_ALIASES = {"fs-1300": "ssl-1300", "ls-1300": "ssl-1300"}
# GCAT short names the audit saw on the leaderboard; none may survive into a cohort name.
BANNED_NAMES = ("Resehetnev", "One Web", "ISRO SAC/Banga")


def test_alias_file_loads_and_every_alias_is_one_hop():
    a = bus.load_curated_aliases()
    assert not set(a.group_codes) & set(a.group_codes.values())
    assert not set(a.bus_slugs) & set(a.bus_slugs.values())
    for code, group in a.group_codes.items():
        assert code != group
        assert a.group_code(group) == group, "a target resolves to itself"


def test_known_manufacturer_aliases_resolve_to_one_group():
    a = bus.load_curated_aliases()
    for code, group in KNOWN_MANUFACTURER_ALIASES.items():
        assert a.group_code(code) == group, code
    # The audit's three NPO PM rows and two Lockheed rows collapse to one group each.
    assert len({a.group_code(c) for c in ("NPOPM", "NPOPMR", "RESH")}) == 1
    assert len({a.group_code(c) for c in ("LAC", "LM")}) == 1
    assert a.group_code("SPXS") == "SPXS", "codes outside the table pass through untouched"


def test_known_display_names_replace_gcat_typos_and_truncations():
    a = bus.load_curated_aliases()
    for code, name in KNOWN_DISPLAY_NAMES.items():
        assert a.display_name(code) == name, code
    for name, _ in a.display.values():
        assert not any(bad in name for bad in BANNED_NAMES)


def test_known_bus_aliases_fold_the_1300_family_into_one_slug():
    a = bus.load_curated_aliases()
    for old, new in KNOWN_BUS_ALIASES.items():
        assert a.bus_slug(old) == new, old
    assert a.bus_slug("ssl-1300") == "ssl-1300"
    assert a.bus_slug("ssl-1300e") == "ssl-1300e", "variants stay distinct (methodology 4.3)"
    assert "SSL-1300" in a.bus_names["ssl-1300"]
    assert "FS-1300" in a.bus_names["ssl-1300"] and "LS-1300" in a.bus_names["ssl-1300"]


def test_every_retired_slug_gets_a_redirect_row():
    a = bus.load_curated_aliases()
    rows = a.slug_alias_rows()
    by_key = {(kind, old): (new, reason) for kind, old, new, reason in rows}
    assert by_key[("manufacturer", "lac")][0] == "lm"
    assert by_key[("manufacturer", "npopmr")][0] == "npopm"
    assert by_key[("manufacturer", "resh")][0] == "npopm"
    assert by_key[("bus", "fs-1300")][0] == "ssl-1300"
    assert by_key[("bus", "ls-1300")][0] == "ssl-1300"
    for (_, old), (new, reason) in by_key.items():
        assert old != new, "benchmark_slug_alias forbids a self-redirect"
        assert reason.startswith("curated alias: ")
    assert len(rows) == len(a.group_codes) + len(a.bus_slugs)


def test_slugify_matches_the_build_sql_rule():
    assert bus.slugify_code("NPOPMR") == "npopmr"
    assert bus.slugify_code("ISRO SAC/Banga") == "isro-sac-banga"
    assert bus.slugify_code("RAYM?") == "raym"


def test_malformed_alias_tables_fail_the_build_loudly(tmp_path):
    chained = tmp_path / "chained.yml"
    chained.write_text(
        "manufacturers:\n  - codes: [A]\n    group: B\n  - codes: [B]\n    group: C\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must not themselves be aliased"):
        bus.load_curated_aliases(chained)
    twice = tmp_path / "twice.yml"
    twice.write_text(
        "manufacturers:\n  - codes: [A]\n    group: B\n  - codes: [A]\n    group: C\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="aliased twice"):
        bus.load_curated_aliases(twice)
    selfie = tmp_path / "self.yml"
    selfie.write_text("buses:\n  - slugs: [x]\n    into: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aliases to itself"):
        bus.load_curated_aliases(selfie)


@pytest.mark.db
def test_curated_manufacturer_cohorts_are_one_group_each(db_conn):
    """Outcome pin on the live build: every leaf code the table aliases lands on its survivor's
    group code and slug, and no retired group code survives anywhere."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT manufacturer_group_code, manufacturer_slug FROM satellite_bus "
            "WHERE manufacturer_code IN ('NPOPM', 'NPOPMR', 'RESH')"
        )
        assert cur.fetchall() == [("NPOPM", "npopm")]
        cur.execute(
            "SELECT count(*) FROM satellite_bus WHERE manufacturer_group_code IN "
            "('NPOPMR', 'RESH', 'LAC')"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT DISTINCT manufacturer_group_code, manufacturer_slug FROM satellite_bus "
            "WHERE manufacturer_code IN ('LMSC', 'LMSS', 'LMSD', 'LM', 'LMSSD')"
        )
        assert cur.fetchall() == [("LM", "lm")]
        cur.execute(
            "SELECT count(*) FROM satellite_bus WHERE rollup_source = 'curated_alias' "
            "AND (rollup_path IS NULL OR rollup_path[array_length(rollup_path, 1)] "
            "     <> manufacturer_group_code)"
        )
        assert cur.fetchone()[0] == 0, "an aliased row's path must end in the surviving code"


@pytest.mark.db
def test_retired_slugs_redirect_and_never_resolve_live(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM v_bus_benchmarks_manufacturer "
            "WHERE manufacturer_slug IN ('npopmr', 'resh', 'lac')"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM v_bus_benchmarks_bus WHERE bus_slug IN ('fs-1300', 'ls-1300')"
        )
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT kind, old_slug, new_slug FROM benchmark_slug_alias")
        rows = set(cur.fetchall())
    for kind, old, new, _ in bus.load_curated_aliases().slug_alias_rows():
        assert (kind, old, new) in rows, f"{kind}/{old} has no redirect to {new}"


@pytest.mark.db
def test_no_banned_gcat_short_name_reaches_a_cohort_name(db_conn):
    with db_conn.cursor() as cur:
        for bad in BANNED_NAMES:
            cur.execute(
                "SELECT count(*) FROM v_bus_benchmarks_manufacturer "
                "WHERE manufacturer_name LIKE %s",
                (f"%{bad}%",),
            )
            assert cur.fetchone()[0] == 0, bad
            cur.execute(
                "SELECT count(*) FROM v_bus_benchmarks_bus WHERE primary_manufacturer LIKE %s",
                (f"%{bad}%",),
            )
            assert cur.fetchone()[0] == 0, bad
        cur.execute(
            "SELECT manufacturer_name, manufacturer_country FROM v_bus_benchmarks_manufacturer "
            "WHERE manufacturer_slug = 'npopm'"
        )
        assert cur.fetchone() == ("ISS Reshetnev (NPO PM)", "RU")
        cur.execute(
            "SELECT manufacturer_name FROM v_bus_benchmarks_manufacturer "
            "WHERE manufacturer_slug = 'lm'"
        )
        assert cur.fetchone() == ("Lockheed Martin",)
