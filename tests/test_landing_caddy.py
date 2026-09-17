"""Real Caddy routing checks; set CADDY_BIN to a local binary to enable."""

import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError

import pytest

from scripts.check_landing import check_landing, fetch

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def landing(tmp_path):
    binary = os.environ.get("CADDY_BIN") or shutil.which("caddy")
    if not binary:
        pytest.skip("set CADDY_BIN for real Caddy integration checks")

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            # An upstream response must retain its body/status, unlike a file-server miss.
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'"upstream missing"')

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
            yield base_url, site
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
    base_url, _ = landing
    status, body, _ = fetch(base_url + "/live/orbital")
    assert status == 404
    assert body == b'"upstream missing"'
    status, body, _ = fetch(base_url + "/live/exo")
    assert status == 502
    assert b"Not on the chart" not in body


def test_missing_recovery_asset_does_not_report_success(landing):
    base_url, site = landing
    (site / "404.html").unlink()
    status, _, _ = fetch(base_url + "/unknown-with-no-recovery-asset")
    assert status == 404
    with pytest.raises(RuntimeError):
        check_landing(base_url)
