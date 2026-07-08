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
