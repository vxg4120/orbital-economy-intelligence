"""Real Caddy routing checks; set CADDY_BIN to a local binary to enable."""

import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from scripts.check_landing import check_landing, fetch

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "/assets/index-vraI7x54.js"
SHELL = b"<!doctype html><title>shell</title>"


@pytest.fixture
def landing(tmp_path):
    binary = os.environ.get("CADDY_BIN") or shutil.which("caddy")
    if not binary:
        pytest.skip("set CADDY_BIN for real Caddy integration checks")

    class Upstream(BaseHTTPRequestHandler):
        """Answers like the real SPA server (api/main.py): an unknown /api/* path keeps its 404,
        a built bundle is served, and every other path gets the SPA shell with a 200, including
        a bundle that is not there."""

        def do_GET(self):
            if self.path.startswith("/api/"):
                # An upstream response must retain its body/status, unlike a file-server miss.
                status, ctype, body = 404, "application/json", b'"upstream missing"'
            elif self.path == BUNDLE:
                status, ctype, body = 200, "text/javascript; charset=utf-8", b"export {};"
            else:
                status, ctype, body = 200, "text/html; charset=utf-8", SHELL
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("ETag", '"stub"')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    # Reserve four distinct ephemeral ports while constructing the local-only config.
    sockets = []
    for _ in range(4):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
    ports = [sock.getsockname()[1] for sock in sockets]
    site = tmp_path / "landing"
    (site / "img").mkdir(parents=True)
    for name in ("index.html", "404.html", "img/portrait.jpg"):
        shutil.copyfile(ROOT / "deploy/landing" / name, site / name)
    config = (ROOT / "deploy/caddy/Caddyfile").read_text()
    config = config.replace(
        "email {$ACME_EMAIL}",
        "admin off\n\tauto_https off\n\tpersist_config off\n\tskip_install_trust",
    )
    for host, port in zip(
        ("www.{$BASE_DOMAIN}", "orbital.{$BASE_DOMAIN}", "exo.{$BASE_DOMAIN}"), ports[1:]
    ):
        config = config.replace(host, f"http://127.0.0.1:{port}")
    base_url = f"http://127.0.0.1:{ports[0]}"
    config = config.replace("{$BASE_DOMAIN}", base_url)
    config = config.replace("/srv/landing", str(site))
    config = config.replace("/data/access", str(tmp_path / "logs"))
    config = config.replace("oei-api:8600", f"127.0.0.1:{upstream.server_port}")
    config = config.replace("exo-api:8700", "127.0.0.1:1")
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(config)
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
    }
    for sock in sockets:
        sock.close()
    with (tmp_path / "caddy.log").open("w") as log:
        process = subprocess.Popen(
            [binary, "run", "--config", str(caddyfile), "--adapter", "caddyfile"],
            stdout=log, stderr=log, env=env,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((tmp_path / "caddy.log").read_text())
                try:
                    fetch(base_url + "/")
                    break
                except URLError:
                    time.sleep(0.05)
            else:
                pytest.fail("Caddy did not start within 5 seconds")
            yield base_url, site, f"http://127.0.0.1:{ports[2]}", f"http://127.0.0.1:{ports[3]}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=5)


def test_styled_404_and_normal_pages(landing):
    check_landing(landing[0])


def test_upstream_response_and_connection_failure_keep_status(landing):
    base_url, *_ = landing
    status, body, _ = fetch(base_url + "/live/orbital")
    assert status == 404
    assert body == b'"upstream missing"'
    status, body, _ = fetch(base_url + "/live/exo")
    assert status == 502
    assert b"Not on the chart" not in body


def test_missing_recovery_asset_does_not_report_success(landing):
    base_url, site, *_ = landing
    (site / "404.html").unlink()
    status, _, _ = fetch(base_url + "/unknown-with-no-recovery-asset")
    assert status == 404
    with pytest.raises(RuntimeError):
        check_landing(base_url)


def _get(url):
    """(status, body, headers) for any status."""
    try:
        response = urlopen(url, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), response.headers


def test_spa_hosts_answer_probes_and_robots_before_the_proxy(landing):
    """The SPA upstream answers every unknown path with the shell and a 200, so a scanner's
    /.env read as found and a crawler's robots.txt was HTML. exo's upstream is down in this
    fixture, so a pass there proves Caddy answers on its own."""
    _, _, orbital, exo = landing
    for host in (orbital, exo):
        for probe in ("/.env", "/.git/config", "/wp-login.php", "/x/.aws/credentials"):
            status, body, _ = _get(host + probe)
            assert (status, body) == (404, b""), (host, probe)
        status, body, headers = _get(host + "/robots.txt")
        assert status == 200
        assert headers["Content-Type"].startswith("text/plain")
        assert body.decode().splitlines() == ["User-agent: *", "Allow: /"]
    # A deep link still reaches the SPA, and a dot inside a segment is not a probe.
    for path in ("/satellites/25544", "/operators/st.engineering"):
        status, body, _ = _get(orbital + path)
        assert (status, body) == (200, SHELL), path


def test_only_a_real_bundle_is_cached_for_a_year(landing):
    """Bundles are content-hashed, so a year of caching can never serve a stale one. But a
    missing bundle comes back as the SPA shell with a 200; cached immutably under its hashed
    name, that shell would break any rollback that brings the name back."""
    _, _, orbital, _ = landing
    status, body, headers = _get(orbital + BUNDLE)
    assert (status, body) == (200, b"export {};")
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert headers["Content-Type"].startswith("text/javascript")
    assert headers["ETag"] == '"stub"'

    status, body, headers = _get(orbital + "/assets/index-DEADBEEF.js")
    assert status == 404
    assert body != SHELL
    assert "immutable" not in (headers["Cache-Control"] or "")

    status, _, headers = _get(orbital + "/")
    assert status == 200
    assert "immutable" not in (headers["Cache-Control"] or "")
