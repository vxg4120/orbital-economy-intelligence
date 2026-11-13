"""Public copy that states a fact the code decides is checked against that code here.

The 2026-11-13 site audit found sentences that had been true when written and were later left
behind by the code: the Conflicts page credited the wrong catalog with the status ranking, the
audit strip named no fleet universe and held a stale FCC count, and the bus correction text
promised a precedence path that was never built. Each test below reads the constant or the config
that a sentence depends on, so that the next change to it fails here instead of on the site.
No database is needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from api.routers import audit, buses, reachability

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _seo_description(pattern: str) -> str:
    routes = json.loads(_read("web/public/seo-routes.json"))["routes"]
    return next(r for r in routes if r["pattern"] == pattern)["description"]


def test_conflicts_status_headline_names_the_catalog_that_ranks_first():
    order = yaml.safe_load(_read("identity/precedence.yml"))["status"]
    headline = _read("web/src/lib/conflicts.ts")
    assert order[0] == "gcat", "the status headline says GCAT ranks first; precedence.yml disagrees"
    assert "The resolver ranks GCAT first for status" in headline
    assert "resolves to decayed" in headline


def test_reachability_copy_states_the_element_age_cutoff_the_query_uses():
    phrase = f"no more than {reachability.MAX_ELEMENT_AGE_DAYS} days old"
    assert phrase in _seo_description("/reachability")
    view = re.sub(r"\s+", " ", _read("web/src/views/Reachability.tsx"))
    assert phrase in view


def test_audit_strip_names_every_fleet_the_lingering_query_counts():
    strip = _read("web/src/components/AuditStrip.tsx")
    note = re.search(r"LINGERING_FLEETS_NOTE =\s*\"([^\"]+)\"", strip)
    assert note, "AuditStrip.tsx must keep its fleet list in LINGERING_FLEETS_NOTE"
    text = note.group(1)
    for name in audit.BENCHMARK_OPERATORS:
        assert name in text, f"{name} is counted by the audit but not named on the page"
    words = {8: "eight"}
    assert words[len(audit.BENCHMARK_OPERATORS)] in text
    assert "eight fleets" in strip


def test_resolver_empty_state_counts_the_same_fleets():
    view = _read("web/src/views/Resolver.tsx")
    assert len(audit.BENCHMARK_OPERATORS) == 8
    assert "for eight operator fleets" in view


def test_kuiper_milestone_matches_the_fcc_order():
    # DA 26-553, para. 3: 1,616 satellites by 2026-07-30 and 3,232 by 2029-07-30.
    assert audit.KUIPER_REQUIRED == 1616
    assert audit.KUIPER_REQUIRED * 2 == 3232
    strip = _read("web/src/components/AuditStrip.tsx")
    assert f'deadline: "{audit.KUIPER_DEADLINE}"' in strip
    assert "deploy all 3,232" in strip and "July 30, 2029" in strip
    fixture = json.loads(_read("web/src/api/fixtures/audit_summary.json"))["kuiper_milestone"]
    assert fixture["required"] == audit.KUIPER_REQUIRED
    assert fixture["deadline"] == audit.KUIPER_DEADLINE


def test_filings_lead_times_are_measured_from_the_filing_and_agree_everywhere():
    # The coverage block is built inside the methodology handler, so its literal is read from source.
    router = _read("api/routers/filings.py")
    view = re.sub(r"\s+", " ", _read("web/src/views/Filings.tsx"))
    months = re.compile(r"about (\d+) months")
    example = re.search(r'"filed_to_launch_lead_time_examples": \((.*?)\),', router, re.S)
    assert example, "the coverage block must keep its lead-time example"
    # Join the implicitly concatenated string pieces, so that a number split across lines counts.
    router_months = months.findall(re.sub(r'"\s+"', "", example.group(1)))
    assert router_months == ["15", "30", "51"]
    assert months.findall(view) == router_months
    # The grant-based figures (about 14 and 38 months) must never be labeled as filed-to-launch.
    assert "filed about 14 months" not in router and "filed about 38 months" not in router


def test_refresh_cadence_copy_matches_the_installed_cron():
    readme = _read("deploy/README.md")
    assert re.search(r"^10 7,19 \* \* \* \S*nightly-refresh\.sh", readme, re.M)
    assert "twice a day, at 07:10 and 19:10 UTC" in buses.REFRESH
    router = _read("api/routers/filings.py")
    assert "inside the twice-daily refresh" in router
    assert "inside the nightly refresh" not in router


def test_bus_correction_text_promises_only_what_is_built():
    order = yaml.safe_load(_read("identity/precedence.yml"))
    # When a bus or manufacturer precedence exists, the correction text can say how it works.
    assert "bus" not in order and "manufacturer" not in order
    assert "operator_confirmed" not in buses.CORRECTION_CHANNEL
    assert "has not been built yet" in buses.CORRECTION_CHANNEL
    view = re.sub(r"\s+", " ", _read("web/src/views/Buses.tsx"))
    assert "outrank catalog sources" not in view
