"""Server-side route metadata for the SPA shell (api/seo.py).

The pure tests run on a small inline shell against the real route table in web/public, so they
need no build. The TestClient tests need the built SPA at web/dist and are skipped without it.
"""

import json
import pathlib
import re
import warnings

import pytest

from api import seo

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

_WEB_DIST = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"
_needs_spa = pytest.mark.skipif(
    not (_WEB_DIST / "index.html").exists(),
    reason="SPA not built at web/dist; catch-all mount is absent",
)

SHELL = """<!doctype html>
<html><head>
    <title>Orbital Economy Terminal</title>
    <meta
      name="description"
      content="root description"
    />
    <link rel="canonical" href="https://orbital.vibcreates.com/" />
    <meta property="og:title" content="Orbital Economy Terminal" />
    <meta property="og:description" content="root description" />
    <meta property="og:url" content="https://orbital.vibcreates.com/" />
    <meta name="twitter:title" content="Orbital Economy Terminal" />
    <meta name="twitter:description" content="root description" />
  </head><body><div id="root"></div></body></html>
"""

# the source table, never the built copy under web/dist (which can lag an edit to public/)
_PUBLIC_ROUTES = pathlib.Path(__file__).resolve().parent.parent / "web" / "public" / "seo-routes.json"
ROUTES = seo.load_routes(_PUBLIC_ROUTES)


def _attr(html, pattern):
    m = re.search(pattern, html, re.S)
    return m.group(1) if m else None


def _head(html):
    return {
        "title": _attr(html, r"<title>(.*?)</title>"),
        "description": _attr(html, r'name="description"\s+content="([^"]*)"'),
        "canonical": _attr(html, r'<link rel="canonical" href="([^"]*)"'),
        "og:title": _attr(html, r'property="og:title" content="([^"]*)"'),
        "og:description": _attr(html, r'property="og:description"\s+content="([^"]*)"'),
        "og:url": _attr(html, r'property="og:url" content="([^"]*)"'),
        "twitter:title": _attr(html, r'name="twitter:title" content="([^"]*)"'),
        "twitter:description": _attr(html, r'name="twitter:description"\s+content="([^"]*)"'),
        "robots": _attr(html, r'name="robots" content="([^"]*)"'),
    }


def test_root_gets_the_landing_description():
    html, status = seo.render_head(SHELL, "/", "", ROUTES)
    h = _head(html)
    assert status == 200
    assert h["title"] == "Orbital Economy Terminal"
    # the landing is a story page, so its description is its own, not the site's catalog line
    table = json.loads(_PUBLIC_ROUTES.read_text())
    root = next(r for r in table["routes"] if r["pattern"] == "/")
    assert h["description"] == root["description"]
    assert h["description"] != table["site"]["description"]
    assert h["canonical"] == "https://orbital.vibcreates.com/"
    assert h["og:url"] == h["canonical"]
    assert h["robots"] is None


def test_deep_route_with_param_rewrites_every_tag():
    html, status = seo.render_head(SHELL, "/resolver/25544", "", ROUTES)
    h = _head(html)
    assert status == 200
    assert h["title"] == "Resolver · Orbital Economy Terminal"
    assert h["canonical"] == "https://orbital.vibcreates.com/resolver/25544"
    assert h["og:url"] == h["canonical"]
    assert h["og:title"] == h["twitter:title"] == h["title"]
    assert h["description"].startswith("Search the resolved satellite catalog")
    assert h["og:description"] == h["twitter:description"] == h["description"]
    assert h["robots"] is None


def test_trailing_slash_matches_and_is_dropped_from_canonical():
    html, status = seo.render_head(SHELL, "/buses/", "", ROUTES)
    assert status == 200
    assert _head(html)["canonical"] == "https://orbital.vibcreates.com/buses"


def test_static_segment_is_case_insensitive_and_canonical_is_lowercase():
    html, status = seo.render_head(SHELL, "/Buses/Starlink-V2-Mini/", "", ROUTES)
    h = _head(html)
    assert status == 200
    assert h["title"] == "Bus benchmarks · Orbital Economy Terminal"
    # The static segment comes from the pattern; the id keeps the case it was asked with.
    assert h["canonical"] == "https://orbital.vibcreates.com/buses/Starlink-V2-Mini"


def test_canonical_keeps_only_the_params_the_route_lists():
    html, _ = seo.render_head(SHELL, "/buses/aerospace-corp", "kind=bus&utm_source=x", ROUTES)
    assert _head(html)["canonical"] == "https://orbital.vibcreates.com/buses/aerospace-corp?kind=bus"
    html, _ = seo.render_head(SHELL, "/resolver/25544", "kind=bus", ROUTES)
    assert _head(html)["canonical"] == "https://orbital.vibcreates.com/resolver/25544"
    # bytes, as the ASGI scope carries it
    html, _ = seo.render_head(SHELL, "/buses/x", b"kind=manufacturer", ROUTES)
    assert _head(html)["canonical"] == "https://orbital.vibcreates.com/buses/x?kind=manufacturer"


def test_unmatched_path_is_404_noindex_without_canonical():
    for path in ("/nope", "/resolver/a/b", "/buses/x/y/z", "/api"):
        html, status = seo.render_head(SHELL, path, "", ROUTES)
        h = _head(html)
        assert status == 404, path
        assert h["robots"] == "noindex", path
        assert h["canonical"] is None, path
        assert h["og:url"] is None, path
        assert h["title"] == "Page not found · Orbital Economy Terminal"
        assert 'data-route-meta' in html


def test_param_values_are_percent_encoded_and_attributes_escaped():
    """A path param goes back out percent-encoded (the client regenerates its canonical from
    the encoded pathname, so both sides agree), and the attribute is HTML-safe either way."""
    html, _ = seo.render_head(SHELL, '/resolver/a"b', "", ROUTES)
    assert "/resolver/a%22b" in html
    assert '/resolver/a"b' not in html
    assert "/resolver/a&quot;b" not in html


def test_every_app_route_is_in_the_table():
    """The patterns the SPA router declares (App.tsx) all resolve to a titled entry."""
    for path in ("/", "/resolver", "/resolver/1", "/conflicts", "/operators", "/operators/7",
                 "/review", "/review/42", "/buses", "/buses/x", "/reachability", "/filings"):
        assert seo.match(ROUTES, path) is not None, path


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@_needs_spa
def test_unknown_path_is_a_real_404_that_still_renders_the_spa(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    h = _head(r.text)
    assert h["robots"] == "noindex"
    assert h["canonical"] is None
    assert '<div id="root"></div>' in r.text


@_needs_spa
def test_landing_is_served_from_the_static_shell_that_matches_the_table(client):
    # the root path never goes through render_head (the static mount serves index.html as it
    # is), so the hand-edited shell has to carry the "/" entry and the site name itself
    r = client.get("/")
    assert r.status_code == 200
    h = _head(r.text)
    table = json.loads(_PUBLIC_ROUTES.read_text())
    root = next(x for x in table["routes"] if x["pattern"] == "/")
    assert h["title"] == root["title"] == h["og:title"] == h["twitter:title"]
    assert h["description"] == root["description"] == h["og:description"] == h["twitter:description"]
    assert _attr(r.text, r'property="og:site_name" content="([^"]*)"') == table["site"]["name"]


@_needs_spa
def test_deep_link_gets_its_own_head(client):
    r = client.get("/buses/x")
    assert r.status_code == 200
    h = _head(r.text)
    assert h["title"] == "Bus benchmarks · Orbital Economy Terminal"
    assert h["canonical"] == "https://orbital.vibcreates.com/buses/x"

    r = client.get("/buses/starlink-v2-mini?kind=bus")
    assert r.status_code == 200
    assert _head(r.text)["canonical"] == "https://orbital.vibcreates.com/buses/starlink-v2-mini?kind=bus"
