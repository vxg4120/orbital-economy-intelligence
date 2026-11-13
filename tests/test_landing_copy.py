"""Content checks for the vibcreates.com landing page, deploy/landing/index.html.

The page is static copy served straight from the repo, so nothing else keeps its claims honest.
Each copy test pins a claim that the 2026-11-13 audit found false or stale, so that a later copy
pass cannot quietly bring it back. The structure tests pin what deploy/landing-snapshot.sh and the
page's own script rely on: one tile per id, and a snapshot date that agrees with the cached stamp.
DB-free; it reads only the file.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "deploy/landing/index.html"

# Every tile the page script overwrites from /live/orbital and /live/exo, which is also every
# tile deploy/landing-snapshot.sh bakes.
LIVE_TILES = (
    "s-sat", "s-gp", "s-cand", "s-assert",
    "r-sat", "r-gp", "r-op", "r-conf",
    "r-star", "r-cand", "r-assert", "r-dconf",
)


class _Text(HTMLParser):
    """The text a reader sees: everything outside <script>, <style> and <head>."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


@pytest.fixture(scope="module")
def html() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def text(html) -> str:
    parser = _Text()
    parser.feed(html)
    return re.sub(r"\s+", " ", "".join(parser.parts))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _label(html: str, tile_id: str) -> str:
    match = re.search(r'id="%s">[^<]*</div><div class="l">([^<]*)</div>' % re.escape(tile_id), html)
    assert match, f"no label found for tile {tile_id}"
    return match.group(1)


def _og_description(html: str) -> str:
    match = re.search(r'<meta property="og:description" content="([^"]*)"', html)
    assert match
    return match.group(1)


# ---- structure --------------------------------------------------------------------------------


def test_json_ld_parses(html):
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert len(blocks) == 1
    json.loads(blocks[0])


def test_every_live_tile_appears_exactly_once(html):
    for tile_id in LIVE_TILES:
        hits = re.findall(r'id="%s">([^<]*)<' % re.escape(tile_id), html)
        assert len(hits) == 1, tile_id
        assert hits[0].strip(), f"{tile_id} has no cached value"


def test_snapshot_date_and_cached_stamp_agree(html):
    """landing-snapshot.sh moves both together; a hand edit that moves one strands the other."""
    date = re.findall(r"cached fallback, snapshot (\d{4})-(\d{2})-\d{2}", html)
    stamp = re.findall(r"cached · ([A-Z][a-z]+) (\d{4})", html)
    assert len(date) == 1 and len(stamp) == 1
    (year, month), (stamp_month, stamp_year) = date[0], stamp[0]
    months = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]
    assert (stamp_month, stamp_year) == (months[int(month) - 1], year)


def test_no_en_or_em_dash(html):
    assert not re.search("[–—]", html)


# ---- the satellites entry ---------------------------------------------------------------------


def test_ownership_disagreement_is_not_attributed_to_space_track(text):
    """Space-Track supplies decay messages and orbital history, never an owner claim: the owner
    disagreement is between CelesTrak's SATCAT and GCAT."""
    assert not re.search(r"Space-Track[^.]*disagree about who owns", text)
    assert "decay messages" in text


def test_no_hardcoded_share_of_disagreeing_objects(text):
    """99.3% was a July 2026 figure that nothing computes and today's data does not reproduce."""
    assert "99.3%" not in text
    sentence = next(s for s in _sentences(text) if s.startswith("Nearly every shared object"))
    assert "%" not in sentence


def test_fcc_lead_times_are_measured_from_the_filing(text):
    """14 and 39 months are grant-to-launch gaps. Measured from the filing, Starlink Gen1 (filed
    2016-11-15) led its first test satellites (2018-02-22) by 15 months, and Kuiper (filed
    2019-07-04) led its first prototypes (2023-10-06) by 51."""
    assert "14 months and Kuiper 39 months" not in text
    assert "filed for Starlink Gen1 15 months before its first test satellites launched" in text
    assert "filed for Kuiper 51 months before its first prototypes launched" in text


def test_fcc_queue_is_dated_as_a_snapshot(text):
    """The IBFS bulk download stopped gaining satellite filings in mid-2025."""
    queue = [s for s in _sentences(text) if "The queue is a snapshot" in s]
    assert len(queue) == 1
    assert "mid-2025" in queue[0]


def test_owner_tile_does_not_claim_operator_attribution(html):
    """operator_pct counts any current owner row, and about a tenth of on-orbit payloads are owned
    only by a country placeholder, so the tile cannot say operators are attributed."""
    label = _label(html, "r-op")
    assert "attribution" not in label
    assert "owner" in label


# ---- the exoplanets entry ---------------------------------------------------------------------


def test_gaia_is_not_named_as_a_reconciled_source(html, text):
    """ExoDossier ingests nothing from Gaia; it only carries Gaia DR3 ids copied from the Archive."""
    assert "cosmos.esa.int" not in html
    assert not re.search(r"reconciles[^.]*Gaia", text)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    graph = json.loads(blocks[0])["@graph"]
    items = next(n for n in graph if n.get("@type") == "ItemList")["itemListElement"]
    exo = next(i["item"] for i in items if i["item"]["name"] == "ExoDossier")
    assert "Gaia" not in exo["description"]


def test_vetting_dossiers_are_not_claimed_as_live(text):
    """ExoDossier's own About page says the dossiers that weigh the evidence come later."""
    assert not re.search(r"generates[^.]*dossiers", text)
    for sentence in _sentences(text):
        if "vetting dossier" in sentence.lower():
            assert "later" in sentence, sentence


def test_no_first_mcp_server_superlative(text):
    """Exoplanet MCP servers were public before ExoDossier's was written on 2026-07-15."""
    assert not re.search(r"first\s+(exoplanet\s+)?MCP", text, re.I)


def test_star_tile_does_not_claim_every_star_was_cross_matched(html):
    """About half of the host stars appear in only one source table."""
    assert "cross-matched" not in _label(html, "r-star")


# ---- what "live" means ------------------------------------------------------------------------


def test_live_claims_are_scoped_to_the_fetched_numbers(html, text):
    """Only the satellite and exoplanet tiles are fetched, and both APIs serve a warm cache."""
    og = _og_description(html)
    assert "The numbers on this page are live" not in og
    assert "satellite and exoplanet numbers" in og
    assert "live queries against my databases" not in text
    assert "real queries that hit my databases" not in text
