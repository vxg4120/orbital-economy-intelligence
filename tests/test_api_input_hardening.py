"""Inputs that used to surface as 500s instead of a clean 401 / 422, exercised without a
database."""

import pytest
from fastapi import HTTPException

from api.routers.review import _require_token
from api.seo import Route, Routes, canonical_for, match, render_head

SHELL = """<!doctype html><html><head><title>x</title>
<meta name="description" content="d" />
<link rel="canonical" href="https://orbital.vibcreates.com/" />
<meta property="og:title" content="t" /><meta property="og:description" content="d" />
<meta property="og:url" content="https://orbital.vibcreates.com/" />
<meta name="twitter:title" content="t" /><meta name="twitter:description" content="d" />
</head><body></body></html>"""


def test_non_ascii_token_is_a_401_not_a_500(monkeypatch):
    """Starlette decodes header bytes as latin-1; hmac.compare_digest on str raised TypeError
    for "ÿ"."""
    monkeypatch.setenv("REVIEW_TOKEN", "secret")
    with pytest.raises(HTTPException) as e:
        _require_token("\xff")
    assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e:
        _require_token("wrong")
    assert e.value.status_code == 401
    _require_token("secret")  # the right token passes


def _routes() -> Routes:
    return Routes(
        origin="https://orbital.vibcreates.com",
        site_name="Orbital",
        routes=(
            Route(pattern="/operators/:operatorId?", title="Operators · Orbital", description="d" * 50),
            Route(pattern="/draft", title="Draft · Orbital", description="d" * 50, noindex=True),
        ),
        not_found=Route(pattern="*", title="Not found · Orbital", description="n" * 50, noindex=True),
    )


def test_canonical_percent_encodes_a_param():
    routes = _routes()
    m = match(routes, "/operators/A B")  # the ASGI path is decoded
    assert m is not None
    assert canonical_for(routes, m) == "https://orbital.vibcreates.com/operators/A%20B"


def test_canonical_keeps_what_the_browser_keeps_in_a_path():
    # location.pathname leaves + ' ( ) , : @ bare and encodes the space; the client takes that
    # pathname as its canonical, so the server-rendered one must agree character for character
    routes = _routes()
    m = match(routes, "/operators/a+b (c), d:e@f")
    assert m is not None
    assert canonical_for(routes, m) == "https://orbital.vibcreates.com/operators/a+b%20(c),%20d:e@f"


def test_matched_noindex_route_is_served_without_a_canonical():
    out, status = render_head(SHELL, "/draft", routes=_routes())
    assert status == 200
    assert 'rel="canonical"' not in out
    assert 'property="og:url"' not in out
    assert 'name="robots" content="noindex"' in out
