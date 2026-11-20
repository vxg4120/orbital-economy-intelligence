"""A decay date is set aside when the object was still being tracked long after it.

An element set is a measurement of an object in orbit, so an object whose latest element set is
dated well after its "decay date" had not decayed on that date. On production on 2026-11-20,
882 objects had both a resolved decay date and an element set, and 9 of them had an element set
dated after the decay date. The gaps were 1 day (five objects), 17 days (one), and then 196,
2,072 and 6,357 days: NORAD 56915 (a Starlink that GCAT records as reentered on 2026-05-07
while SATCAT has it operational and its elements put it at 482 km), 44547 (Yunhai-1 02) and
24946 (Iridium 33), both GCAT status C, "collided", whose parent bodies are still tracked. The
rule is "more than TRACKED_AFTER_DAYS (30) days", which fires on those three and on nothing
else.

The claim does not vote for the decay date, and the same source's DECAYED status does not vote
for the status, so the object stops reading as decayed. The claims stay on the record and on
the card, which marks the date.

Every db test seeds real tables inside db_conn's transaction and rolls back.
"""

import datetime as dt
import sys
import warnings
from pathlib import Path

import pytest
from fastapi import FastAPI
from psycopg.rows import dict_row

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from api.deps import get_db
from api.routers import satellites
from identity import resolve
from identity.decay_claims import TRACKED_AFTER_DAYS, tracked_after
from tests.claimfix import seed_claim

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_graph  # noqa: E402

# Reserved synthetic NORAD ids for this file.
BASE = 971400000


# --- the rule, without a database ----------------------------------------------------------


def test_the_threshold_is_the_measured_one():
    assert TRACKED_AFTER_DAYS == 30


@pytest.mark.parametrize(
    ("epoch", "expected"),
    [
        (dt.date(2026, 5, 6), False),   # tracked only before the date
        (dt.date(2026, 5, 7), False),   # the same day
        (dt.date(2026, 5, 24), False),  # 17 days after: the ordinary lag (Dragon C206.6)
        (dt.date(2026, 6, 6), False),   # 30 days after is not yet a contradiction
        (dt.date(2026, 6, 7), True),    # 31 days after is
        (dt.date(2026, 11, 19), True),  # NORAD 56915
    ],
)
def test_an_element_set_more_than_thirty_days_after_the_date_contradicts_it(epoch, expected):
    assert tracked_after("2026 May  7 1041", epoch) is expected


def test_the_three_production_objects_are_contradicted():
    assert tracked_after("2026 May  7 1041", dt.date(2026, 11, 19)) is True   # 56915
    assert tracked_after("2021 Mar 18 0741", dt.date(2026, 11, 19)) is True   # 44547
    assert tracked_after("2009 Feb 10 1656", dt.date(2026, 7, 8)) is True     # 24946


def test_every_form_of_date_the_sources_give_is_read():
    epoch = dt.date(2026, 11, 19)
    assert tracked_after("2026-01-15", epoch) is True          # SATCAT, Space-Track
    assert tracked_after(dt.date(2026, 1, 15), epoch) is True
    assert tracked_after("2026 Jan 15?", epoch) is True        # GCAT, uncertain but with a day


def test_a_date_that_gives_no_day_is_measured_from_the_end_of_its_span():
    """GCAT sometimes gives only a month or a year, which parses to its first day. The object
    may have decayed at any time in that span, so the span has to be over for more than 30
    days before an element set contradicts it."""
    assert tracked_after("2026 May?", dt.date(2026, 6, 20)) is False   # 20 days after May 31
    assert tracked_after("2026 May?", dt.date(2026, 7, 1)) is True     # 31 days after May 31
    assert tracked_after("2025?", dt.date(2026, 1, 30)) is False       # 30 days after Dec 31
    assert tracked_after("2025?", dt.date(2026, 1, 31)) is True
    assert tracked_after("2025 Dec?", dt.date(2026, 1, 31)) is True    # December rolls the year


def test_nothing_to_compare_is_not_a_contradiction():
    assert tracked_after("2026 May  7", None) is False
    assert tracked_after(None, dt.date(2026, 11, 19)) is False
    assert tracked_after("-", dt.date(2026, 11, 19)) is False
    assert tracked_after("2000s?", dt.date(2026, 11, 19)) is False


def test_an_epoch_with_a_time_is_compared_by_its_utc_day():
    late = dt.datetime(2026, 6, 6, 23, 59, tzinfo=dt.timezone.utc)
    assert tracked_after("2026 May  7", late) is False
    assert tracked_after("2026 May  7", late + dt.timedelta(minutes=2)) is True
    # 2026-06-06 20:00 in Los Angeles is already June 7 in UTC
    pacific = dt.timezone(dt.timedelta(hours=-8))
    assert tracked_after("2026 May  7", dt.datetime(2026, 6, 6, 20, 0, tzinfo=pacific)) is True


def test_the_build_summary_names_the_objects():
    assert build_graph._tracked_after_line([]) == "decay dates set aside, tracked after: 0"
    assert (build_graph._tracked_after_line([24946, 44547, 56915])
            == "decay dates set aside, tracked after: 3  (NORAD 24946, 44547, 56915)")
    assert build_graph._tracked_after_line(list(range(1, 26))).endswith("19, 20 and 5 more)")


# --- through the resolver ------------------------------------------------------------------

# name: (norad offset, {source: (decay date, status)}, latest element set or None)
CASES = {
    # the 56915 shape: GCAT says reentered, SATCAT says operational, elements six months later
    "stale_gcat": (1, {"gcat": ("2026 May  7 1041", "R"), "satcat": (None, "+")},
                   "2026-11-19"),
    # the 24946 shape: collided, the parent body still tracked, SATCAT non-operational
    "collided": (2, {"gcat": ("2009 Feb 10 1656", "C"), "satcat": (None, "-")}, "2026-07-08"),
    # the ordinary lag between a reentry and the last element set published for it
    "recent": (3, {"gcat": ("2026 Jan 15", "R"), "satcat": ("2026-01-15", "D")}, "2026-02-01"),
    # 30 days is not yet a contradiction
    "boundary": (4, {"gcat": ("2026 May  7", "R")}, "2026-06-06"),
    "past_boundary": (5, {"gcat": ("2026 May  7", "R")}, "2026-06-07"),
    # most decayed objects have no element set at all
    "no_elements": (6, {"gcat": ("2001 Feb  3", "R")}, None),
    # one source's date is contradicted and the other's is not: the other's is resolved
    "one_of_two": (7, {"gcat": ("2020 Jan  1", "R"), "satcat": ("2026-11-01", "D")},
                   "2026-11-10"),
    # a decay message is a claim like any other
    "stale_message": (8, {"spacetrack_decay": ("2025-01-07", None), "satcat": (None, "+")},
                      "2026-11-19"),
    # nothing but the stale claim, and no other status to fall through to
    "only_gcat": (9, {"gcat": ("2021 Mar 18 0741", "C")}, "2026-11-19"),
    # a stale date beside an in-orbit status from the same source: only a DECAYED reading is
    # contradicted by tracking, so the status still votes
    "stale_date_in_orbit": (10, {"satcat": ("2020-01-01", "+")}, "2026-11-19"),
}
STATUS = {"R": "DECAYED", "C": "DECAYED", "+": "ACTIVE", "-": "INACTIVE", "D": "DECAYED"}
EXPECT_DATE = {
    "stale_gcat": None, "collided": None, "recent": dt.date(2026, 1, 15),
    "boundary": dt.date(2026, 5, 7), "past_boundary": None, "no_elements": dt.date(2001, 2, 3),
    "one_of_two": dt.date(2026, 11, 1), "stale_message": None, "only_gcat": None,
    "stale_date_in_orbit": None,
}
EXPECT_STATUS = {
    "stale_gcat": "ACTIVE", "collided": "INACTIVE", "recent": "DECAYED", "boundary": "DECAYED",
    "past_boundary": None, "no_elements": "DECAYED", "one_of_two": "DECAYED",
    "stale_message": "ACTIVE", "only_gcat": None, "stale_date_in_orbit": "ACTIVE",
}
SET_ASIDE = {"stale_gcat", "collided", "past_boundary", "one_of_two", "stale_message", "only_gcat",
             "stale_date_in_orbit"}


@pytest.fixture
def seeded(db_conn):
    try:
        ids = {}
        with db_conn.cursor() as cur:
            for src in ("gcat", "satcat"):
                for value, canonical in STATUS.items():
                    if (src == "gcat") == (value in "RC"):
                        cur.execute(
                            "INSERT INTO status_mapping (source, source_value, canonical_status) "
                            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (src, value, canonical))
            cur.execute(
                "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
                "VALUES ('satcat', 'test://tracked-after', now(), now(), 'ok') "
                "RETURNING ingest_run_id")
            run = cur.fetchone()[0]
            for name, (offset, claims, epoch) in CASES.items():
                norad = BASE + offset
                # a date resolved on an earlier night, which the rule has to take back
                cur.execute(
                    "INSERT INTO satellite (norad_id, canonical_name, decay_date) "
                    "VALUES (%s, %s, '1999-09-09') RETURNING satellite_id", (norad, name))
                ids[name] = sat = cur.fetchone()[0]
                seed_claim(cur, sat, "satcat", "name", name, run, key=str(norad))
                # a date under another attribute, which the card must not mark
                seed_claim(cur, sat, "satcat", "object_type", "2020-01-01", run, key=str(norad))
                for source, (date, status) in claims.items():
                    key = f"S{norad}" if source == "gcat" else str(norad)
                    target = None if source == "spacetrack_decay" else sat
                    if date is not None:
                        seed_claim(cur, target, source, "decay_date", date, run, key=key)
                    if status is not None:
                        seed_claim(cur, target, source, "status", status, run, key=key)
                if epoch is not None:
                    cur.execute(
                        "INSERT INTO gp_elements (norad_id, epoch, mean_motion, eccentricity, "
                        "inclination, source) VALUES "
                        "(%s, '2020-01-01T00:00:00Z', 15.5, 0.0004, 53.0, 'test'), "
                        "(%s, %s, 15.5, 0.0004, 53.0, 'test')",
                        (norad, norad, f"{epoch}T12:00:00Z"))
            cur.execute("REFRESH MATERIALIZED VIEW mv_latest_gp_element")
        yield db_conn, ids
    finally:
        db_conn.rollback()


def _resolve(conn):
    stats = {}
    set_aside = resolve._resolve_decay_date(conn, ["spacetrack_decay", "satcat", "gcat"], stats)
    resolve._resolve_status(conn, ["gcat", "satcat"], stats, set_aside)
    return stats


@pytest.mark.db
def test_the_resolver_sets_aside_a_decay_date_the_tracking_contradicts(seeded):
    conn, ids = seeded
    _resolve(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT canonical_name, decay_date FROM satellite "
                    "WHERE satellite_id = ANY(%s)", (list(ids.values()),))
        assert dict(cur.fetchall()) == EXPECT_DATE
        # the claims stay on the record
        cur.execute("SELECT value FROM v_current_claim WHERE satellite_id = %s "
                    "AND attribute = 'decay_date'", (ids["stale_gcat"],))
        assert cur.fetchall() == [("2026 May  7 1041",)]


@pytest.mark.db
def test_the_source_whose_decay_date_is_set_aside_does_not_call_the_object_decayed(seeded):
    """GCAT leads the status precedence, so without this the card would say DECAYED with no
    decay date while the object is tracked at 482 km. The status falls through to the next
    source, and an object with no other status is left unresolved."""
    conn, ids = seeded
    _resolve(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT s.canonical_name, h.canonical_status FROM satellite s "
                    "LEFT JOIN satellite_status_history h USING (satellite_id) "
                    "WHERE s.satellite_id = ANY(%s)", (list(ids.values()),))
        assert dict(cur.fetchall()) == EXPECT_STATUS


@pytest.mark.db
def test_the_stat_and_the_build_summary_name_the_objects(seeded, tmp_path):
    conn, ids = seeded
    stats = _resolve(conn)
    mine = {name for name, sat in ids.items() if sat in stats["decay_dates_tracked_after"]}
    assert mine == SET_ASIDE
    summary = build_graph.summarize(conn, {}, stats, tmp_path / "no_review.csv")
    listed = {n - BASE for n in summary["decay_dates_tracked_after"] if BASE < n < BASE + 100}
    assert listed == {CASES[name][0] for name in SET_ASIDE}
    assert len(summary["decay_dates_tracked_after"]) == len(stats["decay_dates_tracked_after"])


@pytest.mark.db
def test_the_whole_resolver_applies_both_halves(seeded):
    conn, ids = seeded
    stats = resolve.resolve(conn)
    assert ids["stale_gcat"] in stats["decay_dates_tracked_after"]
    with conn.cursor() as cur:
        cur.execute("SELECT s.decay_date, h.canonical_status, h.source FROM satellite s "
                    "JOIN satellite_status_history h USING (satellite_id) "
                    "WHERE s.satellite_id = %s", (ids["stale_gcat"],))
        assert cur.fetchall() == [(None, "ACTIVE", "satcat")]


@pytest.mark.db
def test_the_card_marks_the_claim(seeded):
    """The claim sits under "decay_date" on the card of an object that is not decayed, so the
    response says why it was not used."""
    conn, ids = seeded
    conn.row_factory = dict_row
    app = FastAPI()
    app.include_router(satellites.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: conn
    client = TestClient(app)

    def decay_claims(name):
        body = client.get(f"/api/satellites/{ids[name]}").json()
        return {a["source"]: a for a in body["assertions"] if a["attribute"] == "decay_date"}

    stale = decay_claims("stale_gcat")["gcat"]
    assert stale["tracked_after"] is True
    assert set(stale) == {"attribute", "value", "source", "observed_at", "tracked_after"}
    both = decay_claims("one_of_two")
    assert both["gcat"].get("tracked_after") is True
    assert "tracked_after" not in both["satcat"]
    for name in ("recent", "boundary", "no_elements"):
        assert all("tracked_after" not in a for a in decay_claims(name).values()), name
    # only a decay_date claim is ever marked
    body = client.get(f"/api/satellites/{ids['stale_gcat']}").json()
    assert all("tracked_after" not in a for a in body["assertions"]
               if a["attribute"] != "decay_date")
    # the two dates still disagree, and the card still says so: the mark explains the loser
    assert "decay_date" in client.get(f"/api/satellites/{ids['one_of_two']}").json()["conflicts"]
