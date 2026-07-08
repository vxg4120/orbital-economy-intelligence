"""Parser test for scripts/gen_satcat_codes.py against a trimmed real sources.php fixture.

No network: the fixture is a trimmed, verbatim copy of celestrak.org/satcat/sources.php's table
(header via <th>, rows via <td>, some names wrapped in <a> links, codes with trailing padding).
"""

from pathlib import Path

from scripts import gen_satcat_codes

FIXTURE = Path(__file__).parent / "fixtures" / "satcat_sources_sample.html"


def test_parses_code_to_name_pairs_and_strips_padding():
    codes = gen_satcat_codes.parse_sources_html(FIXTURE.read_text())
    # Codes come back trimmed of the table's cell padding ("US  " -> "US").
    assert codes["US"] == "United States"
    assert codes["CA"] == "Canada"
    assert codes["PRC"] == "People's Republic of China"


def test_extracts_name_text_from_linked_cell():
    """A description wrapped in an <a> tag yields just its text, not markup."""
    codes = gen_satcat_codes.parse_sources_html(FIXTURE.read_text())
    assert codes["AB"] == "Arab Satellite Communications Organization"


def test_keeps_org_codes_needed_for_ma_chains():
    codes = gen_satcat_codes.parse_sources_html(FIXTURE.read_text())
    assert "INTELSAT" in codes["ITSO"]
    assert "EUTELSAT" in codes["EUTE"]


def test_skips_header_row_and_yields_only_data():
    codes = gen_satcat_codes.parse_sources_html(FIXTURE.read_text())
    # The <th> header ("Source Code"/"Source Description") must never appear as a code.
    assert "Source Code" not in codes
    assert "Source" not in codes
    assert len(codes) == 7  # exactly the seven data rows in the fixture


def test_render_yaml_round_trips():
    import yaml

    codes = gen_satcat_codes.parse_sources_html(FIXTURE.read_text())
    rendered = gen_satcat_codes._render_yaml(codes)
    reloaded = yaml.safe_load(rendered)["codes"]
    assert reloaded == codes
