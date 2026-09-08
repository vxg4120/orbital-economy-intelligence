"""Retired cohorts: a published URL that no longer resolves is served from the archive.

A cohort can stop resolving without merging into anything, which benchmark_slug_alias cannot
express because an alias needs a survivor. Observed live: bus/saman, Iran's Saman-1 space tug,
whose object type resolved to ROCKET_BODY while Bus Benchmarks includes payloads only, so the
whole cohort left the live views while sitting in the July and August archives.

These tests use a fake cursor rather than the database, so they run in the network-free CI job:
the point under test is the resolution ORDER and the shape of what gets served, neither of which
needs real rows.
"""

import pytest

from api.routers.buses import _find_archived, _retired_payload

_METRICS = {"fleet_total": 1, "fleet_active": 0, "decayed_count": 1, "decayed_share_pct": 100.0,
            "median_lifetime_years": 0.44, "primary_manufacturer": "ISRC"}


class _FakeCursor:
    """Returns `rows` in order, one per execute; None means a miss for that kind."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        self._last = self._rows.pop(0) if self._rows else None

    def fetchone(self):
        return self._last


class _FakeDB:
    def __init__(self, rows):
        self._cursor = _FakeCursor(rows)

    def cursor(self):
        return self._cursor


def _snapshot_row():
    import datetime as dt

    return {"snapshot_month": dt.date(2026, 8, 1), "display_name": "Saman",
            "metrics": dict(_METRICS), "methodology_version": "1.6"}


def test_find_archived_rebuilds_a_shape_compatible_benchmark():
    """The archived metrics blob plus slug and display name must read like a live row, so the
    same client code renders both without special-casing."""
    kind, benchmark, meta = _find_archived(_FakeDB([None, _snapshot_row()]), "saman", None)
    assert kind == "bus"
    assert benchmark["slug"] == "saman"
    assert benchmark["bus_slug"] == "saman"
    assert benchmark["name"] == "Saman"
    assert benchmark["bus_model"] == "Saman"
    assert benchmark["fleet_total"] == 1
    assert meta["last_published_month"] == "2026-08-01"
    assert meta["methodology_version"] == "1.6"


def test_find_archived_returns_none_when_nothing_was_ever_published():
    """A slug that exists nowhere must stay a 404, not become a blank retired page."""
    assert _find_archived(_FakeDB([None, None]), "never-existed", None) is None


def test_find_archived_honours_an_explicit_kind():
    """?kind=bus must not fall through to the manufacturer namespace."""
    result = _find_archived(_FakeDB([_snapshot_row()]), "saman", "bus")
    assert result is not None and result[0] == "bus"


def test_retired_payload_states_plainly_that_the_figures_are_not_current():
    payload = _retired_payload("bus", "saman", {"slug": "saman", "fleet_total": 1},
                               {"last_published_month": "2026-08-01",
                                "methodology_version": "1.6"})
    assert payload["retired"]["is_retired"] is True
    assert payload["retired"]["last_published_month"] == "2026-08-01"
    assert "not current" in payload["retired"]["explanation"]
    assert payload["aliased_from"] is None      # retirement is not a merge


def test_retired_payload_leaves_live_sections_empty_rather_than_reconstructed():
    """Constituents, orgs, the satellite sample and provenance all read live tables by slug. For
    a cohort that no longer resolves they would be empty at best, and at worst would pick up
    whatever occupies those identifiers now. An honest empty beats a plausible wrong."""
    payload = _retired_payload("bus", "saman", {"slug": "saman"},
                               {"last_published_month": "2026-08-01",
                                "methodology_version": "1.6"})
    assert payload["constituents"] == []
    assert payload["orgs"] == []
    assert payload["satellites_sample"] == []
    assert payload["provenance"] is None
    assert payload["participation"] is None
    assert payload["pending_applications"] is None


def test_retired_payload_keeps_the_correction_channel():
    """Retirement is exactly when a reader is most likely to want to dispute something."""
    payload = _retired_payload("bus", "saman", {"slug": "saman"},
                               {"last_published_month": "2026-08-01",
                                "methodology_version": "1.6"})
    assert payload["correction_channel"]


@pytest.mark.db
def test_a_live_cohort_is_never_shadowed_by_its_own_archive(db_conn):
    """spx is both live and archived three times over. Direct hits win, so the archive fallback
    must never fire for it: serving stale figures for a live cohort is the one way this feature
    could do real damage."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from api.main import app

    body = TestClient(app).get("/api/buses/spx").json()
    assert body.get("retired") is None
    assert body["benchmark"]["fleet_total"] > 0
