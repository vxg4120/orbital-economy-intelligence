"""App-level wiring tests for api/main.py — the SPA catch-all vs. real /api 404s (Task F3).

The SPA fallback (index.html for unmatched paths) is only mounted when a built SPA exists at
web/dist; these tests are skipped otherwise so a clean checkout without a frontend build still
passes. When the mount IS present, unknown /api/* paths must keep their real JSON 404 rather than
being swallowed into the 200 HTML shell (which the JSON client can't parse).
"""

import pathlib
import warnings

import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app

_WEB_DIST = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"
_needs_spa = pytest.mark.skipif(
    not (_WEB_DIST / "index.html").exists(),
    reason="SPA not built at web/dist; catch-all mount is absent",
)


@pytest.fixture
def client():
    return TestClient(app)


@_needs_spa
def test_unknown_api_path_keeps_real_404_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    # A JSON error body, not the SPA's HTML shell.
    assert not r.text.lstrip().startswith("<")

    # Extra path segments on a real route are unmatched too -> still a real 404, not the shell.
    deep = client.get("/api/satellites/25544/extra")
    assert deep.status_code == 404


@_needs_spa
def test_spa_deep_link_still_serves_index_html(client):
    r = client.get("/resolver/25544")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text.lstrip().startswith("<")
