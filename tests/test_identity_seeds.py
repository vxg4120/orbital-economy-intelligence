"""Schema validation for the curated YAML seeds (no DB)."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
OPERATOR_SEED = REPO_ROOT / "identity" / "operator_seed.yml"
STATUS_MAP = REPO_ROOT / "identity" / "status_map.yml"

CANONICAL_STATUS = {"ACTIVE", "PARTIAL", "SPARE", "INACTIVE", "GRAVEYARD", "DECAYED", "UNKNOWN"}
VALID_RELATIONSHIPS = {"subsidiary_of", "brand_of", "acquired_by", "merged_into"}


def _load(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def test_operator_seed_loads_and_has_required_shape():
    doc = _load(OPERATOR_SEED)
    operators = doc["operators"]
    assert len(operators) >= 15, "spec requires >= 15 canonical operators"
    names = set()
    for op in operators:
        assert op["name"], "operator missing name"
        assert op["name"] not in names, f"duplicate operator name: {op['name']}"
        names.add(op["name"])
        assert op["class"] in {"commercial", "civil", "defense", "academic", "mixed"}
        for key in ("aliases", "satcat_codes", "gcat_codes"):
            assert isinstance(op.get(key, []), list)


def test_every_relationship_endpoint_exists_in_operators():
    doc = _load(OPERATOR_SEED)
    names = {op["name"] for op in doc["operators"]}
    for rel in doc["relationships"]:
        assert rel["child"] in names, f"relationship child not an operator: {rel['child']}"
        assert rel["parent"] in names, f"relationship parent not an operator: {rel['parent']}"
        assert rel["relationship"] in VALID_RELATIONSHIPS
        assert rel["valid_from"], "relationship needs a valid_from date"


def test_ma_chains_present_with_verified_dates():
    doc = _load(OPERATOR_SEED)
    chains = {(r["child"], r["parent"]): str(r["valid_from"]) for r in doc["relationships"]}
    assert chains[("OneWeb", "Eutelsat")] == "2023-09-28"
    assert chains[("Inmarsat", "Viasat")] == "2023-05-30"
    assert chains[("Intelsat", "SES")] == "2025-07-17"


def test_status_map_loads_and_uses_only_canonical_statuses():
    doc = _load(STATUS_MAP)
    assert set(doc) >= {"satcat", "gcat", "ucs"}
    for source, codes in doc.items():
        for code, spec in codes.items():
            assert spec["canonical"] in CANONICAL_STATUS, (
                f"{source}:{code} maps to non-canonical status {spec['canonical']!r}"
            )
            assert spec.get("notes"), f"{source}:{code} missing a source-doc note"


# Every phase code in GCAT's phases documentation as read on 2026-11-13
# (https://planet4589.org/space/gcat/web/intro/phases.html). An unmapped code is skipped by the
# resolver, logged as "unmapped status values" every night, and shown on the resolver card as a
# raw value that conflicts with SATCAT; 'TFR' and 'EVA DP' did that for 79 objects.
GCAT_DOCUMENTED_PHASES = {
    "O", "AO", "AO IN", "UDK", "REL", "DEP", "TO", "TOA", "OX",
    "E", "N", "NA", "LEASE",
    "S", "R", "D", "L", "LF", "AS", "AR", "AR IN", "AL", "AL IN", "TX", "F", "AF",
    "DSO", "DSA", "DSA IN", "LO", "LOA",
    "DK", "GRP", "ATT", "TFR", "TFR E", "TFR IN", "C", "EVA DP", "EVA RP", "REFLT",
    "EO", "EAO", "EN", "OI", "OE",
    "ERR",
}
# Phases after which the object no longer exists as itself in orbit (destroyed, reentered,
# landed, crashed, or ended on a surface). Every other documented phase leaves it physically
# present somewhere, so it is no claim about operations and maps to UNKNOWN.
GCAT_END_OF_LIFE_PHASES = {
    "E", "C", "S", "R", "D", "L", "LF", "AS", "AR", "AR IN", "AL", "AL IN", "TX", "F", "AF",
}


def test_every_documented_gcat_phase_is_mapped():
    gcat = _load(STATUS_MAP)["gcat"]
    missing = sorted(GCAT_DOCUMENTED_PHASES - set(gcat))
    assert not missing, f"GCAT phases with no mapping: {missing}"


def test_only_end_of_life_gcat_phases_map_to_decayed():
    gcat = _load(STATUS_MAP)["gcat"]
    decayed = {code for code, spec in gcat.items() if spec["canonical"] == "DECAYED"}
    assert decayed == GCAT_END_OF_LIFE_PHASES
    for code in GCAT_DOCUMENTED_PHASES - GCAT_END_OF_LIFE_PHASES:
        assert gcat[code]["canonical"] == "UNKNOWN", code
