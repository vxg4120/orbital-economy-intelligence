"""A GCAT phase-end date is not a decay date, on every surface that compares decay dates.

GCAT's DDate is the time a phase of an object's history ended, and its Status names the event
that ended it. For a reentry or a landing that is a decay date. For a docking, a transfer into
the ISS, a grapple or a departure to deep space it is the date of that event, and the object's
later history sits under other GCAT entries that are not loaded. The resolver has dropped such
dates since 2026-10-07 (identity/resolve.py, _gcat_ddate_is_decay). The conflict surfaces did
not: on 2026-11-13 they counted 4,334 decay-date conflicts, and for 812 of them the GCAT "decay
date" was a phase date (docked 461, deep space 157, transferred 78, grappled 59, attached 41
and a few others). Only 1 of those 812 was still a conflict between the other sources.

Every test seeds real tables inside db_conn's transaction and rolls back.
"""

import sys
import warnings
from pathlib import Path

import pytest
from fastapi import FastAPI
from psycopg.rows import dict_row

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

import scripts.build_gold_queue as gold
from api.deps import get_db
from api.routers import conflicts, satellites
from identity import resolve
from identity.decay_claims import CURRENT_DECAY_CLAIMS_SQL
from quality.report import _section_decay_date_conflicts
from tests.claimfix import seed_claim

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_graph  # noqa: E402

# Reserved synthetic NORAD ids for this file.
BASE = 971300000

# name: (norad offset, SATCAT decay, GCAT DDate, GCAT status, Space-Track message)
CASES = {
    # a cubesat's entry that ends with its transfer into the ISS (the 40452 shape)
    "transferred": (1, "2015-12-27", "2015 Feb 18", "TFR", None),
    # a real disagreement about a reentry
    "reentered": (2, "2020-01-05", "2020 Jan  3", "R", None),
    # left for deep space in 1964, decayed in 2020 (the OGO A shape)
    "deep_space": (3, "2020-08-29", "1964 Sep  6 0411?", "DSO", None),
    # GCAT's uncertainty mark leaves the phase a reentry
    "probably": (4, "2021-03-01", "2021 Mar  4?", "R?", None),
    # docked in 1983; SATCAT and a Space-Track message still disagree with each other
    "docked_others_differ": (5, "2025-10-08", "1983 Jan", "DK", "2025-10-09"),
    # a GCAT date with no status on its key says nothing about what ended the phase
    "no_status": (6, "2019-06-01", "2019 May  5", None, None),
    # a status code the map does not know is not a decay either
    "unmapped": (7, "2018-02-02", "2018 Jan  1", "ZZ NEW", None),
    # a landing on the same day as SATCAT's decay is agreement
    "landed_same_day": (8, "2022-04-04", "2022 Apr  4 1210", "L", None),
    # grappled, and SATCAT records no decay at all
    "grappled_only": (9, None, "1998 Dec  6 2347:02", "GRP", None),
}
# The cases where two decay claims really differ.
EXPECT_CONFLICT = {"reentered", "probably", "docked_others_differ"}


@pytest.fixture
def seeded(db_conn):
    """{name: satellite_id} for CASES, seeded with the status map from identity/status_map.yml."""
    ids = {}
    try:
        with db_conn.cursor() as cur:
            build_graph.seed_status_map(db_conn)
            cur.execute(
                "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, "
                "rows_ingested, bytes_downloaded, status) "
                "VALUES ('satcat', 'test', now(), now(), 1, 1, 'ok') RETURNING ingest_run_id"
            )
            run = cur.fetchone()[0]
            for name, (offset, satcat, gcat_date, gcat_status, message) in CASES.items():
                norad = BASE + offset
                cur.execute(
                    "INSERT INTO satellite (norad_id, canonical_name, object_type) "
                    "VALUES (%s, %s, 'PAYLOAD') RETURNING satellite_id",
                    (norad, f"ZZ TEST PHASE {name}"),
                )
                sat = ids[name] = cur.fetchone()[0]
                key = f"S{norad}"
                if satcat:
                    seed_claim(cur, sat, "satcat", "decay_date", satcat, run, key=str(norad))
                seed_claim(cur, sat, "gcat", "decay_date", gcat_date, run, key=key)
                if gcat_status:
                    seed_claim(cur, sat, "gcat", "status", gcat_status, run, key=key)
                if message:
                    # Keyed by NORAD id; it reaches the satellite through the SATCAT link.
                    seed_claim(cur, None, "spacetrack_decay", "decay_date", message, run,
                               key=str(norad))
        yield db_conn, ids
    finally:
        db_conn.rollback()


def _names(ids, satellite_ids):
    by_id = {sat: name for name, sat in ids.items()}
    return {by_id[s] for s in satellite_ids if s in by_id}


@pytest.mark.db
def test_only_a_gcat_date_under_a_decayed_phase_is_a_decay_claim(seeded):
    conn, ids = seeded
    with conn.cursor() as cur:
        cur.execute(CURRENT_DECAY_CLAIMS_SQL)
        rows = [r for r in cur.fetchall() if r[0] in ids.values()]
    gcat = _names(ids, [sat for sat, source, _value, _at in rows if source == "gcat"])
    assert gcat == {"reentered", "probably", "landed_same_day"}
    # Every other source's claim is untouched.
    satcat = _names(ids, [sat for sat, source, _value, _at in rows if source == "satcat"])
    assert satcat == set(CASES) - {"grappled_only"}


@pytest.mark.db
def test_the_shared_rule_agrees_with_the_resolver(seeded):
    """The resolver decides in Python (_gcat_ddate_is_decay) and the conflict surfaces decide in
    SQL. They must keep the same GCAT dates, or a card would show a date that the Conflicts page
    says nobody claimed."""
    conn, ids = seeded
    is_decay = resolve._gcat_ddate_is_decay(conn)
    resolver_keeps = _names(ids, [sat for sat in ids.values() if is_decay.get(sat, False)])
    with conn.cursor() as cur:
        cur.execute(CURRENT_DECAY_CLAIMS_SQL)
        sql_keeps = _names(ids, [r[0] for r in cur.fetchall() if r[1] == "gcat"])
    assert sql_keeps == resolver_keeps


@pytest.mark.db
def test_the_conflicts_endpoint_does_not_count_phase_dates(seeded):
    conn, ids = seeded
    conn.row_factory = dict_row
    rows = [r for r in conflicts._decay_conflict_rows(conn) if r["satellite_id"] in ids.values()]
    assert _names(ids, [r["satellite_id"] for r in rows]) == EXPECT_CONFLICT
    by_name = {name: r for r in rows for name, sat in ids.items() if sat == r["satellite_id"]}
    # The docking date is not listed as a decay claim; the two real claims are.
    assert by_name["docked_others_differ"]["sources_and_dates"] == (
        "satcat: 2025-10-08; spacetrack_decay: 2025-10-09"
    )
    assert by_name["reentered"]["sources_and_dates"] == "gcat: 2020 Jan  3; satcat: 2020-01-05"


@pytest.mark.db
def test_the_dq_report_does_not_count_phase_dates(seeded):
    conn, ids = seeded
    with conn.cursor() as cur:
        _cols, rows = _section_decay_date_conflicts(cur)
    mine = {norad - BASE for norad, _name, _claims in rows if norad and BASE < norad < BASE + 100}
    assert mine == {CASES[name][0] for name in EXPECT_CONFLICT}


@pytest.mark.db
def test_the_review_queue_does_not_ask_when_a_docked_object_reentered(seeded):
    """The decay stratum takes the largest SATCAT against GCAT gaps. Phase dates are decades
    from the real decay, so they would fill the stratum with a question that has no answer."""
    conn, ids = seeded
    with conn.cursor() as cur:
        scored = gold._decay_disagreements(cur)
    mine = _names(ids, [sat for _diff, sat, *_rest in scored])
    # docked_others_differ has no GCAT decay claim, and this stratum compares SATCAT with GCAT.
    assert mine == {"reentered", "probably"}


@pytest.mark.db
def test_the_card_does_not_badge_a_phase_date_as_a_decay_conflict(seeded):
    conn, ids = seeded
    conn.row_factory = dict_row
    app = FastAPI()
    app.include_router(satellites.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: conn
    client = TestClient(app)

    def card(name):
        return client.get(f"/api/satellites/{ids[name]}").json()

    for name in CASES:
        body = card(name)
        assert ("decay_date" in body["conflicts"]) == (name in EXPECT_CONFLICT), name
    # The claim itself stays on the card, and the response keeps its four fields.
    transferred = card("transferred")
    gcat_dates = [a for a in transferred["assertions"]
                  if a["attribute"] == "decay_date" and a["source"] == "gcat"]
    assert [a["value"] for a in gcat_dates] == ["2015 Feb 18"]
    assert set(gcat_dates[0]) == {"attribute", "value", "source", "observed_at"}


def test_a_gcat_date_is_compared_only_when_its_own_key_ended_in_a_decay():
    """The card's rule without a database: the status is read on the same GCAT key as the date."""
    claims = [
        {"attribute": "decay_date", "source": "gcat", "source_key": "S1", "value": "2015 Feb 18"},
        {"attribute": "decay_date", "source": "satcat", "source_key": "1", "value": "2015-12-27"},
        {"attribute": "status", "source": "gcat", "source_key": "S1", "value": "TFR"},
    ]
    assert satellites._comparable_claims(claims, gcat_decay_keys=set()) == claims[1:]
    assert satellites._comparable_claims(claims, gcat_decay_keys={"S1"}) == claims
    # Another key's reentry does not make this key's date a decay.
    assert satellites._comparable_claims(claims, gcat_decay_keys={"S2"}) == claims[1:]


@pytest.mark.db
def test_the_audit_report_counts_two_decay_sources_without_phase_dates(seeded):
    """The published conflict rate is conflicts over objects that two sources date. A phase
    date must leave both the numerator and the denominator, or the rate keeps 812 objects that
    only one source dates."""
    from quality import audit_report

    conn, ids = seeded
    with conn.cursor() as cur:
        with_phase_dates = audit_report._catalog_integrity(cur)["decay"]
        # Take the nine seeded objects out again: the difference is what they add.
        cur.execute("DELETE FROM claim WHERE source_key = ANY(%s)",
                    ([f"S{BASE + c[0]}" for c in CASES.values()]
                     + [str(BASE + c[0]) for c in CASES.values()],))
        baseline = audit_report._catalog_integrity(cur)["decay"]
    added_conflicts = with_phase_dates[0] - baseline[0]
    added_denominator = with_phase_dates[1] - baseline[1]
    assert added_conflicts == len(EXPECT_CONFLICT)
    # Two real decay sources: reentered, probably, landed_same_day and docked_others_differ.
    assert added_denominator == 4
