"""The SQL key functions must be twins of their python definitions, proven not assumed.

oei_name_key exists so set-based SQL (churn detection, promotion gates) can normalize names the
same way identity/normalize.py does. Two implementations of one normalization is a standing
drift hazard, so the corpus test runs the FULL latest payload snapshot through both and demands
zero mismatches; a regex divergence that only bites one weird name in 27,000 still fails.
"""

import pytest

from identity.normalize import norm_name


@pytest.mark.db
def test_oei_launch_key_forms(db_conn):
    cases = [
        ("2026-156BH", "2026-156"),
        ("2026-56A", "2026-056"),   # zero-padded, not truncated to a calendar year
        ("1998-067A", "1998-067"),
        ("2026 156BU", "2026-156"),
        ("garbage", None),
        ("", None),
        (None, None),
    ]
    with db_conn.cursor() as cur:
        for piece, expected in cases:
            cur.execute("SELECT oei_launch_key(%s)", (piece,))
            assert cur.fetchone()[0] == expected, f"oei_launch_key({piece!r})"


@pytest.mark.db
def test_oei_name_key_matches_norm_name_over_the_full_corpus(db_conn):
    """Every payload name in the latest OK GCAT snapshot, both implementations, zero mismatches."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(pl_name, name) AS nm, oei_name_key(COALESCE(pl_name, name)) AS sqlk
            FROM raw_gcat_satcat
            WHERE ingest_run_id = (
                SELECT max(r.ingest_run_id) FROM raw_gcat_satcat r
                JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok')
              AND object_type LIKE 'P%'
            """
        )
        rows = cur.fetchall()
    assert len(rows) > 10000
    mismatches = []
    for nm, sqlk in rows:
        pyk = norm_name(nm) or None
        if pyk != sqlk:
            mismatches.append((nm, pyk, sqlk))
    assert not mismatches, f"{len(mismatches)} divergent keys, first: {mismatches[:3]}"


@pytest.mark.db
def test_churn_detection_is_idempotent(db_conn):
    """Running detect twice adds nothing: the ledger records observations, not run counts."""
    from identity import churn

    first = churn.detect(db_conn)
    second = churn.detect(db_conn)
    db_conn.rollback()
    assert second == 0, f"second detect() wrote {second} rows over identical snapshots"
    assert first >= 0


@pytest.mark.db
def test_stability_is_a_measurement_with_denominators(db_conn):
    """Every stability row carries observations >= changes >= anchored-changes, and the standing
    empirical claim holds: referent changes land on unanchored rows."""
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT source, id_type, observations, referent_changes, changes_anchored "
            "FROM key_stability"
        )
        rows = cur.fetchall()
    if not rows:
        pytest.skip("no stability measurements yet in this database")
    for source, id_type, obs, changes, anchored in rows:
        assert obs >= changes >= anchored >= 0, (source, id_type)
        assert changes == 0 or anchored < changes, (
            f"{source}/{id_type}: churn landed mostly on anchored rows, which breaks the "
            "anchored-keys-are-stable premise the expiry rule depends on"
        )


@pytest.mark.db
def test_expiry_never_touches_anchored_satellites(db_conn):
    """The three-way conjunction in expire_contested: whatever it retired, none of it may point
    at an anchored satellite."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM satellite_identifier si
            JOIN satellite s ON s.satellite_id = si.satellite_id
            WHERE si.valid_to IS NOT NULL
              AND si.id_type IN ('gcat_id', 'cospar')
              AND s.anchor_state = 'anchored'
              AND EXISTS (SELECT 1 FROM identity_event e
                          WHERE e.event = 'identifier_expired'
                            AND e.details->>'id_value' = si.id_value
                            AND e.details->>'id_type' = si.id_type)
            """
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.db
def test_anchor_state_matches_norad_presence(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM satellite "
            "WHERE norad_id IS NOT NULL AND anchor_state <> 'anchored'"
        )
        assert cur.fetchone()[0] == 0, "a satellite with a NORAD id is anchored by definition"
