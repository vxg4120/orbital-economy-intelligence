"""Read-only HTTP smoke check: python scripts/check_landing.py BASE_URL."""

import argparse
import uuid
from urllib.error import HTTPError
from urllib.request import urlopen


def fetch(url: str) -> tuple[int, bytes, str]:
    try:
        response = urlopen(url, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), response.headers.get("Content-Type", "")


def check_landing(base_url: str) -> None:
    base_url = base_url.rstrip("/")
    missing = f"/__landing_smoke_{uuid.uuid4().hex}"
    for path, expected, styled in [
        ("/", 200, False),
        ("/img/portrait.jpg", 200, False),
        ("/404.html", 200, True),
        (missing, 404, True),
        (missing + "/", 404, True),
    ]:
        status, body, content_type = fetch(base_url + path)
        if status != expected or not body:
            raise RuntimeError(f"{path}: expected nonempty HTTP {expected}, got {status}/{len(body)} bytes")
        if styled and not (
            "text/html" in content_type
            and b"Not on the chart" in body
            and b'href="/"' in body
        ):
            raise RuntimeError(f"{path}: missing styled recovery page or home link")
        print(f"PASS {path}: HTTP {status}, {len(body)} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    check_landing(parser.parse_args().base_url)
