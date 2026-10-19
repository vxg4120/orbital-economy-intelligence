"""Server-side route metadata for the SPA shell (api/seo.py).

The pure tests run on a small inline shell against the real route table in web/public, so they
need no build. The TestClient tests need the built SPA at web/dist and are skipped without it.
"""

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
    <title>Orbital Economy Intelligence</title>
    <meta
      name="description"
      content="root description"
    />
    <link rel="canonical" href="https://orbital.vibcreates.com/" />
    <meta property="og:title" content="Orbital Economy Intelligence" />
    <meta property="og:description" content="root description" />
    <meta property="og:url" content="https://orbital.vibcreates.com/" />
    <meta name="twitter:title" content="Orbital Economy Intelligence" />
    <meta name="twitter:description" content="root description" />
  </head><body><div id="root"></div></body></html>
"""

ROUTES = seo.load_routes()


def _attr(html, pattern):
    m = re.search(pattern, html, re.S)
    return m.group(1) if m else None


def _head(html):
    return {
        "title": _attr(html, r"<title>(.*?)</title>"),
        "description": _attr(html, r'name="description"\s+content="([^"]*)"'),
        "canonical": _attr(html, r'<link rel="canonical" href="([^"]*)"'),
        "og:title": _attr(html, r'property="og:title" content="([^"]*)"'),
        "og:description": _attr(html, r'property="og:description" content="([^"]*)"'),
        "og:url": _attr(html, r'property="og:url" content="([^"]*)"'),
        "twitter:title": _attr(html, r'name="twitter:title" content="([^"]*)"'),
        "twitter:description": _attr(html, r'name="twitter:description" content="([^"]*)"'),
        "robots": _attr(html, r'name="robots" content="([^"]*)"'),
    }


def test_root_keeps_root_metadata():
    html, status = seo.render_head(SHELL, "/", "", ROUTES)
    h = _head(html)
    assert status == 200
    assert h["title"] == "Orbital Economy Intelligence"
    assert h["canonical"] == "https://orbital.vibcreates.com/"
    assert h["og:url"] == h["canonical"]
    assert h["robots"] is None


def test_deep_route_with_param_rewrites_every_tag():
    html, status = seo.render_head(SHELL, "/resolver/25544", "", ROUTES)
    h = _head(html)
    assert status == 200
    assert h["title"] == "Resolver · Orbital Economy Intelligence"
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
    assert h["title"] == "Bus Benchmarks · Orbital Economy Intelligence"
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
        assert h["title"] == "Page not found · Orbital Economy Intelligence"
        assert 'data-route-meta' in html


def test_values_are_html_escaped():
    html, _ = seo.render_head(SHELL, '/resolver/a"b', "", ROUTES)
    assert '/resolver/a&quot;b' in html
    assert '/resolver/a"b' not in html


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
def test_deep_link_gets_its_own_head(client):
    r = client.get("/buses/x")
    assert r.status_code == 200
    h = _head(r.text)
    assert h["title"] == "Bus Benchmarks · Orbital Economy Intelligence"
    assert h["canonical"] == "https://orbital.vibcreates.com/buses/x"

    r = client.get("/buses/starlink-v2-mini?kind=bus")
    assert r.status_code == 200
    assert _head(r.text)["canonical"] == "https://orbital.vibcreates.com/buses/starlink-v2-mini?kind=bus"
