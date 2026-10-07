"""One GCAT key, one satellite (docs/specs/gcat-sibling-links.md).

The NORAD pass in identity/match.py is additive and never retires a link, so when GCAT moves a
key to a deployment sibling the old link stays, current, at confidence 1.00: 105 keys on
production on 2026-10-06. The churn pass `expire_moved_gcat_keys` retires the link that
disagrees with GCAT's newest row, and three companions make that stick: the assertion writer
ignores retired links, the NORAD pass revives a link GCAT moves back, and the gold queue reads
current keys only. Every test seeds its own rows and rolls back.
"""

import pytest

from identity import assertions, churn, match
from tests.claimfix import seed_claim

pytestmark = pytest.mark.db


def _run(cur, source="gcat"):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, status) "
        "VALUES (%s, 'test://siblings', now(), now(), 'ok') RETURNING ingest_run_id",
        (source,),
    )
    return cur.fetchone()[0]


def _sat(cur, norad, cospar, name):
    cur.execute(
        "INSERT INTO satellite (norad_id, cospar_id, canonical_name, anchor_state, "
        "anchor_source) VALUES (%s, %s, %s, 'anchored', 'satcat') RETURNING satellite_id",
        (norad, cospar, name),
    )
    return cur.fetchone()[0]


def _link(cur, sat, id_type, value, valid_to=None):
    cur.execute(
        "INSERT INTO satellite_identifier (satellite_id, id_type, id_value, source, valid_to) "
        "VALUES (%s, %s, %s, 'gcat', %s)",
        (sat, id_type, value, valid_to),
    )


def _gcat_row(cur, run, jcat, norad, piece, name="SIB"):
    cur.execute(
        "INSERT INTO raw_gcat_satcat (jcat, norad_id, piece, name, pl_name, ingest_run_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (jcat, norad, piece, name, name, run),
    )


def _current(cur, id_type, value):
    cur.execute(
        "SELECT satellite_id FROM satellite_identifier WHERE id_type = %s AND id_value = %s "
        "AND source = 'gcat' AND valid_to IS NULL ORDER BY 1",
        (id_type, value),
    )
    return [r[0] for r in cur.fetchall()]


def _siblings(cur):
    """Two anchored cubesats deployed together, with GCAT's key S<b> wrongly on both (the
    production shape), plus a's own key. Returns (a, b, run)."""
    a = _sat(cur, 970001001, "2026-001A", "ZZ SIB A")
    b = _sat(cur, 970001002, "2026-001B", "ZZ SIB B")
    _link(cur, a, "gcat_id", "S970001001")
    _link(cur, a, "gcat_id", "S970001002")  # the stale sibling link
    _link(cur, b, "gcat_id", "S970001002")
    _link(cur, a, "cospar", "2026-001B")  # and its stale cospar twin
    _link(cur, b, "cospar", "2026-001B")
    return a, b, _run(cur)


def test_the_link_that_disagrees_with_gcats_norad_is_retired(db_conn):
    try:
        with db_conn.cursor() as cur:
            a, b, run = _siblings(cur)
            _gcat_row(cur, run, "S970001002", 970001002, "2026-001B")
            assert churn.expire_moved_gcat_keys(db_conn) == 2
            assert _current(cur, "gcat_id", "S970001002") == [b]
            assert _current(cur, "cospar", "2026-001B") == [b]
            assert _current(cur, "gcat_id", "S970001001") == [a]  # a keeps its own key
            cur.execute(
                "SELECT satellite_id, details->>'id_value', (details->>'moved_to')::bigint "
                "FROM identity_event WHERE rule_fired = 'gcat_anchor_moved' AND satellite_id = %s "
                "ORDER BY 2",
                (a,),
            )
            assert cur.fetchall() == [(a, "2026-001B", b), (a, "S970001002", b)]
            assert churn.expire_moved_gcat_keys(db_conn) == 0  # idempotent
    finally:
        db_conn.rollback()


def test_a_key_without_a_norad_anchors_by_its_own_number(db_conn):
    """GCAT's S<n> is catalog number n. For the ISS-deployed cubesats GCAT publishes no NORAD
    and its COSPAR piece letter is off by one from SATCAT's, so the number is the only tie."""
    try:
        with db_conn.cursor() as cur:
            a, b, run = _siblings(cur)
            _gcat_row(cur, run, "S970001002", None, "1998-067ZZ")
            assert churn.expire_moved_gcat_keys(db_conn) == 1
            assert _current(cur, "gcat_id", "S970001002") == [b]
            # GCAT's piece is not the piece our links carry, so the cospar twin is not judged.
            assert _current(cur, "cospar", "2026-001B") == [a, b]
    finally:
        db_conn.rollback()


def test_nothing_is_retired_when_the_anchor_does_not_hold_the_key(db_conn):
    """A key with one current link, on the wrong satellite, is not retired into having none:
    the matcher links the anchor first, and the next run retires the other."""
    try:
        with db_conn.cursor() as cur:
            a = _sat(cur, 970001011, "2026-002A", "ZZ LONE A")
            _sat(cur, 970001012, "2026-002B", "ZZ LONE B")
            _link(cur, a, "gcat_id", "S970001012")
            run = _run(cur)
            _gcat_row(cur, run, "S970001012", 970001012, "2026-002B")
            assert churn.expire_moved_gcat_keys(db_conn) == 0
            assert _current(cur, "gcat_id", "S970001012") == [a]
    finally:
        db_conn.rollback()


def test_the_norad_pass_revives_a_link_gcat_moves_back(db_conn):
    try:
        with db_conn.cursor() as cur:
            b = _sat(cur, 970001022, "2026-003B", "ZZ BACK B")
            _link(cur, b, "gcat_id", "S970001022", valid_to="2026-10-01")
            run = _run(cur)
            _gcat_row(cur, run, "S970001022", 970001022, "2026-003B", name="ZZ BACK B")
        match._deterministic_gcat_norad(db_conn)
        with db_conn.cursor() as cur:
            assert _current(cur, "gcat_id", "S970001022") == [b]
    finally:
        db_conn.rollback()


def test_a_retired_link_receives_no_claims(db_conn):
    try:
        with db_conn.cursor() as cur:
            a = _sat(cur, 970001031, "2026-004A", "ZZ CLAIM A")
            b = _sat(cur, 970001032, "2026-004B", "ZZ CLAIM B")
            _link(cur, a, "gcat_id", "S970001032", valid_to="2026-10-01")
            _link(cur, b, "gcat_id", "S970001032")
            run = _run(cur)
            _gcat_row(cur, run, "S970001032", 970001032, "2026-004B", name="ZZ CLAIM B")
            cur.execute(
                "UPDATE raw_gcat_satcat SET owner = 'ZZOWN' WHERE jcat = 'S970001032' "
                "AND ingest_run_id = %s",
                (run,),
            )
        assertions.extract(db_conn)
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT satellite_id FROM source_assertion WHERE source = 'gcat' "
                "AND source_key = 'S970001032' AND attribute = 'owner'"
            )
            assert [r[0] for r in cur.fetchall()] == [b]
    finally:
        db_conn.rollback()


def test_a_piece_shared_by_two_anchors_judges_nothing(db_conn):
    """Two GCAT rows with different NORADs and the same piece give the cospar key two anchors.
    Judging it would retire both satellites' links and leave none; the key is skipped."""
    try:
        with db_conn.cursor() as cur:
            a = _sat(cur, 970001041, "2026-005A", "ZZ SHARE A")
            b = _sat(cur, 970001042, "2026-005B", "ZZ SHARE B")
            _link(cur, a, "cospar", "2026-005A")
            _link(cur, b, "cospar", "2026-005A")
            run = _run(cur)
            _gcat_row(cur, run, "S970001041", 970001041, "2026-005A")
            _gcat_row(cur, run, "S970001042", 970001042, "2026-005A")
            assert churn.expire_moved_gcat_keys(db_conn) == 0
            assert _current(cur, "cospar", "2026-005A") == [a, b]
    finally:
        db_conn.rollback()


def test_the_cospar_pass_links_a_numbered_key_by_its_number(db_conn):
    """Without this the NORAD-less ISS rows would be re-linked to the neighbour by piece every
    morning and retired by churn every night, forever."""
    try:
        with db_conn.cursor() as cur:
            a = _sat(cur, 970001051, "1998-067AA", "ZZ ISS A")
            b = _sat(cur, 970001052, "1998-067AB", "ZZ ISS B")
            _link(cur, a, "cospar", "1998-067AB")  # SATCAT's piece for a is GCAT's for b
            run = _run(cur)
            _gcat_row(cur, run, "S970001052", None, "1998-067AB", name="ZZ ISS B")
        match._cospar_pass(db_conn)
        with db_conn.cursor() as cur:
            assert _current(cur, "gcat_id", "S970001052") == [b]
            cur.execute(
                "SELECT rule_fired FROM merge_log WHERE surviving_id = %s "
                "AND details->>'id_value' = 'S970001052'",
                (b,),
            )
            assert cur.fetchall() == [("jcat_number",)]
    finally:
        db_conn.rollback()


def test_a_key_moved_and_moved_back_leaves_a_full_audit_trail(db_conn):
    try:
        with db_conn.cursor() as cur:
            a = _sat(cur, 970001061, "2026-006A", "ZZ TRIP A")
            b = _sat(cur, 970001062, "2026-006B", "ZZ TRIP B")
            _link(cur, a, "gcat_id", "S970001062")
            _link(cur, b, "gcat_id", "S970001062")
            run1 = _run(cur)
            _gcat_row(cur, run1, "S970001062", 970001062, "2026-006B")
            assert churn.expire_moved_gcat_keys(db_conn) == 1
            assert _current(cur, "gcat_id", "S970001062") == [b]
            # GCAT moves the key back to a: the NORAD pass revives a's link, churn retires b's.
            run2 = _run(cur)
            _gcat_row(cur, run2, "S970001062", 970001061, "2026-006A")
        match._deterministic_gcat_norad(db_conn)
        with db_conn.cursor() as cur:
            assert sorted(_current(cur, "gcat_id", "S970001062")) == [a, b]
            assert churn.expire_moved_gcat_keys(db_conn) == 1
            assert _current(cur, "gcat_id", "S970001062") == [a]
            cur.execute(
                "SELECT satellite_id, event, rule_fired FROM identity_event "
                "WHERE details->>'id_value' = 'S970001062' ORDER BY identity_event_id"
            )
            assert cur.fetchall() == [
                (a, "identifier_expired", "gcat_anchor_moved"),
                (a, "identifier_revived", "norad_exact"),
                (b, "identifier_expired", "gcat_anchor_moved"),
            ]
    finally:
        db_conn.rollback()


def test_a_claim_through_a_retired_link_is_not_a_current_claim(db_conn):
    """claim_is_current: the resolver, the satellite page and the conflicts page keep claims
    only through keys that currently identify the satellite, so a sibling's leftover claim
    stops counting the moment its link is retired, even for an attribute the satellite's own
    key never asserts."""
    try:
        with db_conn.cursor() as cur:
            a = _sat(cur, 970001071, "2026-007A", "ZZ VIEW A")
            _link(cur, a, "gcat_id", "S970001071")
            _link(cur, a, "gcat_id", "S970001072", valid_to="2026-10-01")
            run = _run(cur)
            cur.execute(
                "INSERT INTO source_assertion (satellite_id, source_key, attribute, value, "
                "source, observed_at, ingest_run_id) VALUES "
                "(%(a)s, 'S970001071', 'owner', 'OWN', 'gcat', now(), %(r)s), "
                "(%(a)s, 'S970001072', 'manufacturer', 'SIBLING', 'gcat', now(), %(r)s)",
                {"a": a, "r": run},
            )
            cur.execute(
                "SELECT attribute, value FROM source_assertion WHERE satellite_id = %s "
                "AND claim_is_current(satellite_id, source, source_key)",
                (a,),
            )
            assert cur.fetchall() == [("owner", "OWN")]
            # A source with no crosswalk key type (a correction channel) is always current.
            cur.execute("SELECT claim_is_current(%s, 'operator_confirmed', 'fix-1')", (a,))
            assert cur.fetchone()[0] is True
    finally:
        db_conn.rollback()


def test_the_cospar_pass_links_a_numbered_key_even_without_a_piece(db_conn):
    """A NORAD-less row with no usable piece used to skip this pass and fall to fuzzy name
    matching, which could re-link the sibling for churn to retire again (Codex verify)."""
    try:
        with db_conn.cursor() as cur:
            b = _sat(cur, 970001082, "2026-008B", "ZZ NOPIECE B")
            run = _run(cur)
            _gcat_row(cur, run, "S970001082", None, None, name="ZZ NOPIECE B")
        match._cospar_pass(db_conn)
        with db_conn.cursor() as cur:
            assert _current(cur, "gcat_id", "S970001082") == [b]
            assert all(p["id_value"] != "S970001082" for p in match._probes(db_conn))
    finally:
        db_conn.rollback()


def test_a_decay_date_with_no_current_claim_is_retracted(db_conn):
    try:
        with db_conn.cursor() as cur:
            a = _sat(cur, 970001091, "2026-009A", "ZZ DECAY A")
            cur.execute("UPDATE satellite SET decay_date = '2026-01-01' WHERE satellite_id = %s",
                        (a,))
            _link(cur, a, "gcat_id", "S970001092", valid_to="2026-10-01")  # the sibling's key
            run = _run(cur)
            seed_claim(cur, None, "gcat", "decay_date", "2026-01-01", run, key="S970001092")
        from identity import resolve
        resolve._resolve_decay_date(db_conn, ["spacetrack_decay", "satcat", "gcat"])
        with db_conn.cursor() as cur:
            cur.execute("SELECT decay_date FROM satellite WHERE satellite_id = %s", (a,))
            assert cur.fetchone()[0] is None
    finally:
        db_conn.rollback()
