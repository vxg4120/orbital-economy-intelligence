"""Contract + known-data tests for /api/satellites/search and /api/satellites/{id} (Task F1)."""

import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app

# The International Space Station: a stable known object for exact-lookup spot checks.
ISS_NORAD = 25544
ISS_COSPAR = "1998-067A"

_SEARCH_ROW_KEYS = {"satellite_id", "norad_id", "cospar_id", "canonical_name", "object_type",
                    "launch_date", "decay_date", "operator_name", "canonical_status"}


@pytest.fixture
def client(db_conn):
    return TestClient(app)


@pytest.mark.db
def test_search_by_norad_returns_iss(client):
    r = client.get(f"/api/satellites/search?q={ISS_NORAD}")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    row = results[0]
    assert _SEARCH_ROW_KEYS <= set(row)
    assert row["norad_id"] == ISS_NORAD
    assert row["cospar_id"] == ISS_COSPAR


@pytest.mark.db
def test_search_by_cospar_is_case_insensitive(client):
    r = client.get(f"/api/satellites/search?q={ISS_COSPAR.lower()}")
    assert r.status_code == 200
    results = r.json()["results"]
    assert any(row["norad_id"] == ISS_NORAD for row in results)


@pytest.mark.db
def test_search_by_name_returns_shaped_rows(client):
    r = client.get("/api/satellites/search?q=Starlink")
    assert r.status_code == 200
    results = r.json()["results"]
    assert results, "name search should return matches for a common constellation"
    assert len(results) <= 20
    for row in results:
        assert _SEARCH_ROW_KEYS <= set(row)
        assert "starlink" in row["canonical_name"].lower()


@pytest.mark.db
def test_search_missing_query_is_422(client):
    assert client.get("/api/satellites/search").status_code == 422


@pytest.mark.db
def test_detail_deep_object(client):
    # Resolve the ISS satellite_id via search, then fetch its identity card.
    sat_id = client.get(f"/api/satellites/search?q={ISS_NORAD}").json()["results"][0]["satellite_id"]
    r = client.get(f"/api/satellites/{sat_id}")
    assert r.status_code == 200
    body = r.json()

    for key in ("satellite", "identifiers", "ownership", "status_history", "assertions",
                "conflicts", "latest_elements", "merge_events"):
        assert key in body

    assert body["satellite"]["norad_id"] == ISS_NORAD
    assert body["identifiers"], "ISS must have a non-empty identifier crosswalk"
    for ident in body["identifiers"]:
        assert {"id_type", "id_value", "source", "confidence", "valid_from", "valid_to"} <= set(ident)
    assert body["ownership"], "ISS must have a non-empty ownership history"
    for own in body["ownership"]:
        assert {"operator_id", "operator_name", "role", "valid_from", "valid_to", "source",
                "confidence"} <= set(own)
    assert isinstance(body["conflicts"], list)
    # ISS has current GP elements: the latest orbit line must be present and shaped.
    assert body["latest_elements"] is not None
    assert {"epoch", "semi_major_axis_km", "apogee_km", "perigee_km", "inclination",
            "eccentricity", "mean_motion"} <= set(body["latest_elements"])


@pytest.mark.db
def test_detail_scd2_two_segment_ownership(client):
    # A stale-owner satellite carries an acquisition split: one closed segment (valid_to set) and
    # one current segment (valid_to NULL) -- the SCD2 temporal-ownership mechanic, made visible.
    stale = client.get("/api/conflicts/stale-owners?limit=1").json()["rows"][0]
    body = client.get(f"/api/satellites/{stale['satellite_id']}").json()
    owner_segments = [o for o in body["ownership"] if o["role"] == "owner"]
    assert len(owner_segments) >= 2
    assert any(o["valid_to"] is not None for o in owner_segments)
    assert any(o["valid_to"] is None for o in owner_segments)


@pytest.mark.db
def test_detail_null_norad_is_graceful(client, db_conn):
    # Analyst objects can have a NULL norad_id; the detail view must render (no elements, no 500).
    with db_conn.cursor() as cur:
        cur.execute("SELECT satellite_id FROM satellite WHERE norad_id IS NULL LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("no NULL-norad satellite in this build")
    body = client.get(f"/api/satellites/{row[0]}").json()
    assert body["satellite"]["norad_id"] is None
    assert body["latest_elements"] is None


@pytest.mark.db
def test_detail_unknown_id_is_404(client):
    assert client.get("/api/satellites/999999999").status_code == 404


# ---------------------------------------------------------------------------------------------
# conflict semantics: canonical, never raw (the Gravitas false-positive class)
# ---------------------------------------------------------------------------------------------

from api.routers.satellites import _conflict_attributes  # noqa: E402

_STATUS_MAP = {("gcat", "O"): "UNKNOWN", ("satcat", "+"): "ACTIVE", ("satcat", "-"): "INACTIVE",
               ("gcat", "AO"): "ACTIVE"}


def _assertion(attr, source, value):
    return {"attribute": attr, "source": source, "value": value}


def test_case_only_name_difference_is_not_a_conflict():
    """Gravitas vs GRAVITAS: the two catalogs case names differently, and the platform's own
    name matcher (norm_name) has always treated them as the same name."""
    rows = [_assertion("name", "gcat", "Gravitas"), _assertion("name", "satcat", "GRAVITAS")]
    assert _conflict_attributes(rows, _STATUS_MAP) == []


def test_vocabulary_only_object_type_difference_is_not_a_conflict():
    """GCAT 'P' and SATCAT 'PAY' are two vocabularies for PAYLOAD, per canonical_object_type."""
    rows = [_assertion("object_type", "gcat", "P"), _assertion("object_type", "satcat", "PAY")]
    assert _conflict_attributes(rows, _STATUS_MAP) == []


def test_gcat_in_orbit_status_is_a_no_claim_not_a_disagreement():
    """GCAT 'O' means in orbit, which says nothing about operations (it canonicalizes to
    UNKNOWN); SATCAT '+' means active. A no-claim cannot disagree, matching the headline
    conflict machinery's UNKNOWN-excluded semantics."""
    rows = [_assertion("status", "gcat", "O"), _assertion("status", "satcat", "+")]
    assert _conflict_attributes(rows, _STATUS_MAP) == []


def test_satcat_owner_jurisdiction_code_is_not_commensurable():
    """SATCAT's owner vocabulary is jurisdiction-coarse ('US'); GCAT's is an organization
    ('K2SP'). A country and a company are not rival claims about the same thing."""
    rows = [_assertion("owner", "gcat", "K2SP"), _assertion("owner", "satcat", "US")]
    assert _conflict_attributes(rows, _STATUS_MAP) == []


def test_format_only_decay_date_difference_is_not_a_conflict():
    rows = [_assertion("decay_date", "gcat", "2026 Aug  1"),
            _assertion("decay_date", "satcat", "2026-08-01")]
    assert _conflict_attributes(rows, _STATUS_MAP) == []


def test_genuine_disagreements_still_flag():
    """The fix must not eat real conflicts: distinct canonical statuses, genuinely different
    names, different real dates, and disagreeing organization owners all still badge."""
    assert _conflict_attributes(
        [_assertion("status", "gcat", "AO"), _assertion("status", "satcat", "-")],
        _STATUS_MAP) == ["status"]
    assert _conflict_attributes(
        [_assertion("name", "gcat", "Gravitas"), _assertion("name", "satcat", "GRAVITY-1")],
        _STATUS_MAP) == ["name"]
    assert _conflict_attributes(
        [_assertion("decay_date", "gcat", "2026 Aug 1"),
         _assertion("decay_date", "satcat", "2026-08-03")], _STATUS_MAP) == ["decay_date"]
    assert _conflict_attributes(
        [_assertion("owner", "gcat", "K2SP"), _assertion("owner", "ucs", "Rocket Lab")],
        _STATUS_MAP) == ["owner"]


def test_unmapped_status_codes_still_surface():
    """A novel status code must not vanish into the no-claim bucket: unmapped raw values stay
    comparable, so gcat AO (ACTIVE) against an unmapped satcat code still flags."""
    rows = [_assertion("status", "gcat", "AO"), _assertion("status", "satcat", "WEIRD_NEW")]
    assert _conflict_attributes(rows, _STATUS_MAP) == ["status"]


@pytest.mark.db
def test_known_real_status_conflict_still_flags(client):
    """Enhanced CRYSTAL 2105 (satellite_id 23728) is the repo's canonical real status conflict,
    referenced as such in the Resolver view's example list. The canonicalized badge must keep
    flagging it: this pins that the fix removed the false positives without eating true ones."""
    r = client.get("/api/satellites/23728")
    assert r.status_code == 200
    assert "status" in r.json()["conflicts"]
