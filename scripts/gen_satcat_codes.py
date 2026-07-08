#!/usr/bin/env python
"""Generate identity/satcat_owner_codes.yml from CelesTrak's SATCAT sources documentation.

CelesTrak publishes the meaning of every SATCAT `OWNER`/source code at
https://celestrak.org/satcat/sources.php as an HTML table (code -> owning entity / country). This
is a one-time *documentation* read (not a data pull, so no ledger): fetch the page once, parse the
committed/saved HTML into a `code: name` map, and write it to a committed YAML the operator
enrichment consumes. A trimmed copy of the real page is saved as a test fixture so the parser has a
regression test that never hits the network.

Usage:
    python scripts/gen_satcat_codes.py            # fetch live (the one sanctioned doc read)
    python scripts/gen_satcat_codes.py --html PATH  # parse a saved HTML file (no network)
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from html.parser import HTMLParser

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

SOURCES_URL = "https://celestrak.org/satcat/sources.php"
OUT_PATH = pathlib.Path(__file__).resolve().parent.parent / "identity" / "satcat_owner_codes.yml"


class _SourcesTableParser(HTMLParser):
    """Extract (code, name) pairs from the sources.php table.

    The page is a table of two-column rows: an owner code (e.g. `US`, `ITSO`, `PRC`) and its
    expansion (e.g. `United States`, `International Telecommunications Satellite Organization`). We
    collect the text of each `<td>` and pair them up per `<tr>`; the header row (`Source`/`Name` or
    similar) and any row that isn't a clean 2-cell code/name pair are dropped.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_row = False
        self._in_cell = False
        self._cells: list[str] = []
        self._buf: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_row = True
            self._cells = []
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "td" and self._in_cell:
            self._in_cell = False
            self._cells.append("".join(self._buf).strip())
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._cells:
                self.rows.append(self._cells)

    def handle_data(self, data):
        if self._in_cell:
            self._buf.append(data)


def parse_sources_html(html: str) -> dict[str, str]:
    """Parse sources.php HTML into an ordered {code: name} mapping.

    Keeps only rows whose first cell is a plausible owner code (short, no spaces) with a non-empty
    name in the second cell; skips the header and any malformed rows.
    """
    parser = _SourcesTableParser()
    parser.feed(html)
    out: dict[str, str] = {}
    for cells in parser.rows:
        if len(cells) < 2:
            continue
        code, name = cells[0].strip(), cells[1].strip()
        if not code or not name:
            continue
        if " " in code or len(code) > 12:
            continue  # header ("Source") or a non-code row
        if code.casefold() in {"source", "code"}:
            continue
        out.setdefault(code, name)
    return out


def _render_yaml(codes: dict[str, str]) -> str:
    lines = [
        "# SATCAT owner/source codes -> owning entity, from CelesTrak's documentation page",
        "# https://celestrak.org/satcat/sources.php (read once; regenerate with",
        "# scripts/gen_satcat_codes.py). Consumed by identity/enrich_operators.py: each code is",
        "# aliased to the operator resolved for its name (seed match, then GCAT-org name match,",
        "# else a new operator with source='satcat_sources'). Note: most SATCAT codes are",
        "# country-coarse (US, PRC, CIS) — that coarseness is exactly what the graph exists to fix.",
        "codes:",
    ]
    for code in sorted(codes):
        name = codes[code].replace('"', "'")
        lines.append(f'  {_quote(code)}: "{name}"')
    return "\n".join(lines) + "\n"


def _quote(code: str) -> str:
    # Quote codes YAML would misread (pure digits, or those starting with special chars).
    return f'"{code}"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", help="Parse a saved HTML file instead of fetching live.")
    parser.add_argument("--out", default=str(OUT_PATH), help="Output YAML path.")
    args = parser.parse_args(argv)

    if args.html:
        html = pathlib.Path(args.html).read_text()
    else:
        import requests

        from ingest.runlog import USER_AGENT

        resp = requests.get(SOURCES_URL, timeout=120, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        html = resp.text

    codes = parse_sources_html(html)
    if not codes:
        print("No codes parsed — page structure may have changed.", file=sys.stderr)
        return 1
    pathlib.Path(args.out).write_text(_render_yaml(codes))
    print(f"Wrote {len(codes)} SATCAT owner codes to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
