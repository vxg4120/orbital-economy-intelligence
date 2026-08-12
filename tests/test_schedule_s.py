"""Deterministic Schedule S parsing.

The fixture text below is verbatim from real harvested documents (pypdf rendering), because the
whole argument for parsing these with a regex instead of a model is that the FCC's filing tool
emits a fixed layout. If that assumption is wrong these tests are the place it shows up.

Detection is tested against the trap it was written to avoid: the generated Tech Report contains no
"Schedule S" or "Form 312" string anywhere, so anchoring on the form's name -- the obvious first
guess, and the one an outside reviewer proposed -- matches zero real documents.
"""

from ingest import schedule_s

# Verbatim from SATAMD2022063000067, "Sched S Tech Report.pdf", page 1.
PAGE_1 = """Approved by OMB 3060-0678
Estimated Burden: up to 80 hours
312 File Number: SATAMD2022063000067
Filing Description Question Response
Description Applicant seeks authority to launch and operate the remaining 25
SV of an Earth Exploration Satellite Service (EESS) system.
Select Orbit Type NGSO
Space Station or Satellite Network Name Landmapper
Estimated Lifetime of Satellite(s) From Date of Launch 15 Years
Will the space station(s) operate on a Common Carrier basis? No
Total Number of Satellites in the active constellation 25
"""

# Verbatim from SATMOD2025061100144 "Narrative.pdf" -- prose, no parameters.
NARRATIVE_PAGE = """Before the FEDERAL COMMUNICATIONS COMMISSION
Washington, D.C. 20554
By operating at low and very low altitudes, SpaceX's MSS system will enable small spot beams
and greater satellite diversity, wherever they are and whatever they are doing.
"""

ORBITAL_PAGE = """Inclination Angle 97.5 degrees
Argument of Perigee 0.0 degrees
Apogee 525.0 km
Perigee 525.0 km
Inclination Angle 45.0 degrees
Argument of Perigee 0.0 degrees
Apogee 1030.0 km
Perigee 1030.0 km
"""

# Verbatim pypdf rendering of the frequency table from SATAMD2022063000067 page 3. Note it comes
# out ONE CELL PER LINE, and the service label wraps across two of them. This is the real shape;
# a one-row-per-line fixture would have hidden the truncation bug this text exposes. The column
# header is kept in the fixture on purpose: it is what the capture ran into once the group was
# allowed to cross newlines.
BAND_PAGE = """Nature of service Description Frequency Band(s)
Mode
Type
Earth Exploration-Satellite
Service
25500.0 MHz
-27000.0 MHz
Transmit
Earth Exploration-Satellite
Service
2025.0 MHz -2110.0
MHz
Receive
Space Operation Service
400.15 MHz -401.0 MHz
Transmit
"""


def test_detects_schedule_s_by_real_anchors_not_the_form_name():
    """The generated report never names itself. Anchoring on "Schedule S" matches 0 of 3 real
    documents; these anchors were read off the documents themselves and match 3 of 3."""
    assert "Schedule S" not in PAGE_1
    assert "Form 312" not in PAGE_1
    assert schedule_s.is_schedule_s([PAGE_1]) is True


def test_does_not_mistake_a_narrative_for_schedule_s():
    assert schedule_s.is_schedule_s([NARRATIVE_PAGE]) is False


def test_parses_scalars_with_page_citations():
    got = schedule_s.parse_scalars([PAGE_1])
    assert got["orbit_type"] == "NGSO"
    assert got["network_name"] == "Landmapper"
    assert got["lifetime_years"] == 15
    assert got["total_satellites"] == 25
    assert got["orbit_type_page"] == 1
    assert got["total_satellites_page"] == 1


def test_absent_scalars_are_none_and_carry_no_citation():
    """Abstention has to be real. A field we could not find must not acquire a page number,
    because a citation on a guessed value is the failure this whole layer is built to avoid."""
    got = schedule_s.parse_scalars([NARRATIVE_PAGE])
    assert got["orbit_type"] is None
    assert got["total_satellites"] is None
    assert got["orbit_type_page"] is None
    assert got["total_satellites_page"] is None


def test_scalar_page_citation_points_at_the_page_it_was_found_on():
    got = schedule_s.parse_scalars([NARRATIVE_PAGE, PAGE_1])
    assert got["orbit_type"] == "NGSO"
    assert got["orbit_type_page"] == 2


def test_parses_each_orbital_plane_as_its_own_row():
    planes = schedule_s.parse_planes([ORBITAL_PAGE])
    assert len(planes) == 2
    assert planes[0]["inclination_deg"] == 97.5
    assert planes[0]["apogee_km"] == 525.0
    assert planes[0]["perigee_km"] == 525.0
    assert planes[1]["inclination_deg"] == 45.0
    assert planes[1]["apogee_km"] == 1030.0
    assert all(p["source_page"] == 1 for p in planes)
    assert [p["plane_idx"] for p in planes] == [0, 1]


def test_no_planes_parsed_from_prose():
    """"low and very low altitudes" must never become an altitude. The unit anchor is what stops
    it: the generated report always emits "<label> <number> <unit>" on one line."""
    assert schedule_s.parse_planes([NARRATIVE_PAGE]) == []


def test_parses_frequency_bands_with_direction():
    bands = schedule_s.parse_bands([BAND_PAGE])
    assert len(bands) == 3
    assert bands[0]["freq_low_mhz"] == 25500.0
    assert bands[0]["freq_high_mhz"] == 27000.0
    assert bands[0]["direction"] == "Transmit"
    assert bands[1]["direction"] == "Receive"
    assert bands[1]["freq_low_mhz"] == 2025.0
    assert bands[2]["freq_low_mhz"] == 400.15
    assert bands[2]["service"] == "Space Operation Service"
    assert [b["band_idx"] for b in bands] == [0, 1, 2]


def test_wrapped_service_label_is_captured_whole():
    """The table renders one cell per line, so "Earth Exploration-Satellite" and "Service" arrive
    on separate lines. Keeping only the trailing fragment yields the useless service name
    "Service", and it survives citation validation because that word IS on the page. Presence is
    not completeness, so the parse has to be right rather than merely checkable."""
    bands = schedule_s.parse_bands([BAND_PAGE])
    assert bands[0]["service"] == "Earth Exploration-Satellite Service"
    assert bands[1]["service"] == "Earth Exploration-Satellite Service"
    assert all("\n" not in b["service"] for b in bands)


def test_service_capture_does_not_swallow_the_column_header():
    """Letting the label cross newlines made the first match start up in the header row, producing
    "Nature of service Description Frequency Band(s) Mode Type Earth Exploration-Satellite
    Service". Bounding the capture to a two-line, label-sized span is what stops it."""
    bands = schedule_s.parse_bands([BAND_PAGE])
    for band in bands:
        assert "Nature of service" not in band["service"]
        assert "Frequency Band" not in band["service"]
        assert len(band["service"]) < 60
