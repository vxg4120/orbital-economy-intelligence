"""GET /api/stories: the landing's story facts (docs/specs/landing-stories.md).

The shape test is DB-marked and read-only (a GET through TestClient, nothing seeded). The rest
exercise the pure helpers that carry the honesty rules: which month the constellation series
stop at, which months are partial, and how country-level owners are kept out of the ranking.
"""

import datetime as dt
import warnings

import pytest

# TestClient's transport import emits a StarletteDeprecationWarning at import time; suppress it
# here so the module imports cleanly under `pytest -W error` (same as tests/test_api_stats.py).
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.main import app
from api.routers.stories import (
    complete_through,
    is_partial,
    month_end,
    next_month_start,
    pending_after,
    split_fleet,
    trim_series,
)


@pytest.fixture
def client(db_conn):  # db_conn: skips when the dev DB is unreachable
    return TestClient(app)


@pytest.mark.db
@pytest.mark.graph
def test_stories_shape(client):
    r = client.get("/api/stories")
    assert r.status_code == 200
    body = r.json()

    assert set(body) >= {
        "as_of", "launches_monthly", "last_30d", "recent_launches", "reentries",
        "constellations", "attribution", "active_fleet", "storms",
    }
    dt.date.fromisoformat(body["as_of"])

    months = body["launches_monthly"]
    assert len(months) == 13
    assert [m["month"] for m in months] == sorted(m["month"] for m in months)
    assert months[-1]["month"] == body["as_of"][:7]
    assert all(not m["partial"] for m in months[:-1])
    for m in months:
        assert set(m) == {"month", "launched", "reentered", "partial"}

    assert set(body["last_30d"]) == {"launches", "payloads_launched", "payloads_reentered"}
    assert body["last_30d"]["payloads_launched"] >= body["last_30d"]["launches"]

    launches = body["recent_launches"]
    assert 0 < len(launches) <= 10
    for g in launches:
        assert set(g) >= {"launch_key", "date", "lead_name", "payloads", "objects", "owner",
                          "owner_attributed"}
        assert g["payloads"] >= 1 and g["objects"] >= g["payloads"]
        assert isinstance(g["owner_attributed"], bool)
    assert [g["date"] for g in launches] == sorted((g["date"] for g in launches), reverse=True)

    re = body["reentries"]
    assert re["window_days"] == 30
    assert re["payloads"] == body["last_30d"]["payloads_reentered"]
    assert re["starlink_v1"] <= re["starlink"] <= re["payloads"]
    assert len(re["notable"]) <= 8
    assert all(not n["name"].lower().startswith("starlink") for n in re["notable"])

    keys = [c["key"] for c in body["constellations"]]
    assert keys == ["starlink", "oneweb", "kuiper", "qianfan", "guowang"]
    att = body["attribution"]
    assert set(att) == {"pending_payloads", "since", "complete_through"}
    for c in body["constellations"]:
        assert set(c) == {"key", "operator", "in_orbit_now", "added_12m", "series"}
        assert len(c["series"]) <= 24
        if c["series"]:
            assert c["series"][-1]["month"] == att["complete_through"]

    fleet = body["active_fleet"]
    assert len(fleet["top"]) <= 6
    assert fleet["total"] == sum(t["n"] for t in fleet["top"]) + fleet["country_only"] + fleet[
        "other"
    ]
    assert fleet["top"][0]["operator"] == "SpaceX"  # the real build: over half the active fleet

    for s in body["storms"]:
        assert s["level"] in {"G1", "G2", "G3", "G4", "G5"}
        assert isinstance(s["kp_max"], float)


# ---- pure helpers ------------------------------------------------------------------------------


def _m(month, launched, attributed):
    return {"month": month, "launched": launched, "attributed": attributed}


def test_complete_through_stops_before_the_first_lagging_month():
    months = [
        _m("2026-05", 360, 338),
        _m("2026-06", 379, 373),
        _m("2026-07", 428, 240),  # 56%: GCAT has not attributed these yet
        _m("2026-08", 326, 1),
        _m("2026-09", 249, 5),
    ]
    assert complete_through(months) == "2026-06"


def test_complete_through_ignores_a_well_attributed_month_after_the_gap():
    # a later month that happens to pass must not extend the series across a lagging month,
    # because its month-end count would still be missing the earlier month's payloads
    months = [_m("2026-06", 100, 95), _m("2026-07", 100, 10), _m("2026-08", 20, 20)]
    assert complete_through(months) == "2026-06"


def test_complete_through_threshold_edge_and_empty_months():
    assert complete_through([_m("2026-01", 10, 8), _m("2026-02", 10, 7)]) == "2026-01"
    assert complete_through([_m("2026-01", 10, 9), _m("2026-02", 0, 0)]) == "2026-02"
    assert complete_through([_m("2026-01", 10, 1)]) is None
    assert complete_through([]) is None


def test_pending_after_counts_only_months_past_complete_through():
    months = [_m("2026-06", 379, 373), _m("2026-07", 428, 240), _m("2026-08", 326, 1)]
    assert pending_after(months, "2026-06") == (428 - 240) + (326 - 1)
    assert pending_after(months, None) == 6 + 188 + 325


def test_month_helpers():
    assert month_end("2026-02") == dt.date(2026, 2, 28)
    assert month_end("2024-02") == dt.date(2024, 2, 29)
    assert month_end("2026-12") == dt.date(2026, 12, 31)
    assert next_month_start("2026-06") == dt.date(2026, 7, 1)
    assert next_month_start("2026-12") == dt.date(2027, 1, 1)


def test_partial_month_flags():
    ref = dt.date(2026, 10, 16)
    assert is_partial("2026-10", ref) is True
    assert is_partial("2026-09", ref) is False
    # the month is whole once the reference date reaches its last day
    assert is_partial("2026-10", dt.date(2026, 10, 31)) is False
    assert is_partial("2026-11", ref) is True


def test_trim_series_keeps_the_newest_months_through_the_cutoff():
    rows = [{"month": f"2025-{m:02d}", "in_orbit": m} for m in range(1, 13)]
    kept = trim_series(rows, "2025-10", n=3)
    assert [r["month"] for r in kept] == ["2025-08", "2025-09", "2025-10"]
    assert trim_series(rows, None) == []


def test_split_fleet_keeps_country_placeholders_out_of_the_ranking():
    rows = [
        {"owner": "SpaceX", "attributed": True, "n": 600},
        {"owner": "United States", "attributed": False, "n": 150},
        {"owner": "Eutelsat", "attributed": True, "n": 100},
        {"owner": "Amazon", "attributed": True, "n": 50},
        {"owner": None, "attributed": False, "n": 25},
        {"owner": "Planet Labs", "attributed": True, "n": 75},
    ]
    out = split_fleet(rows, top_n=2)
    assert out["total"] == 1000
    assert [t["operator"] for t in out["top"]] == ["SpaceX", "Eutelsat"]
    assert out["top"][0]["pct"] == 60.0
    assert out["country_only"] == 175
    assert out["other"] == 125  # Amazon + Planet Labs
