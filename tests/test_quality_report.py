"""Tests for quality/report.py -- the Data Quality & Conflict Report generator (Task 5).

DB-backed. Seeds a synthetic fixture purely against the schema (no import from identity/):
satellites, operators, assertions, and an acquisition relationship, exercising every report
section. Everything is inserted inside db_conn's transaction and rolled back in a finally block
so nothing is ever committed to the shared dev DB.
"""

import re

import pytest

from quality.report import (
    _section_decay_date_conflicts,
    _section_stale_post_ma_owners,
    _section_status_disagreements,
    _section_supgp_cross_tags,
    generate_report,
    write_report,
)

# Reserved synthetic norad-id range for this task's db tests (distinct from test_metrics.py's
# range so the two test modules' fixtures never collide within a shared dev DB).
NORAD_STATUS_DISAGREE = 920200001
NORAD_DECAY_CONFLICT = 920200002
NORAD_STALE_OWNER = 920200003
NORAD_ON_ORBIT_RICH = 920200004
NORAD_ON_ORBIT_BARE = 920200005
NORAD_DECAYED = 920200006


def _insert_satellite(cur, norad_id, name, object_type="PAYLOAD", decay_date=None):
    cur.execute(
        "INSERT INTO satellite (norad_id, cospar_id, canonical_name, object_type, "
        "launch_date, decay_date) VALUES (%s, %s, %s, %s, '2018-01-01', %s) "
        "RETURNING satellite_id",
        (norad_id, f"2018-{norad_id % 1000:03d}A", name, object_type, decay_date),
    )
    return cur.fetchone()[0]


def _insert_ingest_run(cur, source, endpoint, status="ok"):
    cur.execute(
        "INSERT INTO ingest_run (source, endpoint, started_at, finished_at, rows_ingested, "
        "bytes_downloaded, status) VALUES (%s, %s, now(), now(), 10, 1000, %s) "
        "RETURNING ingest_run_id",
        (source, endpoint, status),
    )
    return cur.fetchone()[0]


@pytest.fixture
def seeded(db_conn):
    """Seeds the full synthetic fixture and yields the open cursor's connection.

    Rolls back on teardown -- nothing seeded here is ever committed.
    """
    with db_conn.cursor() as cur:
        run_satcat = _insert_ingest_run(cur, "satcat", "https://celestrak.org/pub/satcat.csv")
        run_gcat = _insert_ingest_run(
            cur, "gcat", "https://planet4589.org/space/gcat/tsv/cat/satcat.tsv"
        )
        _insert_ingest_run(cur, "gp", "https://celestrak.org/NORAD/elements/gp.php", "skipped_fresh")

        # --- Section: status disagreements (SATCAT vs GCAT) ---
        # The section compares each source's *asserted* status, mapped to canonical via
        # status_mapping (not the resolver's single winning status), so seed both: SATCAT still
        # calls the object ACTIVE while GCAT records it as reentered (DECAYED).
        sat_disagree = _insert_satellite(cur, NORAD_STATUS_DISAGREE, "ZZ TEST STATUS DISAGREE")
        cur.execute(
            "INSERT INTO status_mapping (source, source_value, canonical_status) VALUES "
            "('satcat', 'ZZACT', 'ACTIVE'), ('gcat', 'ZZDEC', 'DECAYED') "
            "ON CONFLICT (source, source_value) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO source_assertion "
            "(satellite_id, source_key, attribute, value, source, observed_at, ingest_run_id) "
            "VALUES "
            "(%s, %s, 'status', 'ZZACT', 'satcat', now(), %s), "
            "(%s, %s, 'status', 'ZZDEC', 'gcat', now(), %s)",
            (
                sat_disagree, str(NORAD_STATUS_DISAGREE), run_satcat,
                sat_disagree, str(NORAD_STATUS_DISAGREE), run_gcat,
            ),
        )
        # A resolved on-orbit status keeps this object in the coverage denominator too.
        cur.execute(
            "INSERT INTO satellite_status_history "
            "(satellite_id, canonical_status, observed_at, source) VALUES "
            "(%s, 'ACTIVE', now(), 'satcat')",
            (sat_disagree,),
        )

        # --- Section: decay-date conflicts ---
        sat_decay = _insert_satellite(cur, NORAD_DECAY_CONFLICT, "ZZ TEST DECAY CONFLICT")
        cur.execute(
            "INSERT INTO source_assertion "
            "(satellite_id, source_key, attribute, value, source, observed_at, ingest_run_id) "
            "VALUES "
            "(%s, %s, 'decay_date', '2024-01-15', 'satcat', now(), %s), "
            "(%s, %s, 'decay_date', '2024-02-20', 'gcat', now(), %s)",
            (
                sat_decay, str(NORAD_DECAY_CONFLICT), run_satcat,
                sat_decay, str(NORAD_DECAY_CONFLICT), run_gcat,
            ),
        )

        # --- Section: stale post-M&A owners ---
        sat_stale = _insert_satellite(cur, NORAD_STALE_OWNER, "ZZ TEST STALE OWNER SAT")
        cur.execute(
            "INSERT INTO operator (canonical_name, country, operator_class) VALUES "
            "('ZZ Test Child Co', 'US', 'commercial'), ('ZZ Test Parent Co', 'US', 'commercial') "
            "RETURNING operator_id",
        )
        child_id, parent_id = [r[0] for r in cur.fetchall()]
        cur.execute(
            "INSERT INTO operator_alias (operator_id, alias, source) VALUES (%s, %s, 'satcat')",
            (child_id, "ZZTESTCHILD"),
        )
        cur.execute(
            "INSERT INTO operator_relationship "
            "(child_id, parent_id, relationship, valid_from, source) "
            "VALUES (%s, %s, 'acquired_by', '2023-01-01', 'test')",
            (child_id, parent_id),
        )
        cur.execute(
            "INSERT INTO source_assertion "
            "(satellite_id, source_key, attribute, value, source, observed_at, ingest_run_id) "
            "VALUES (%s, %s, 'owner', 'ZZTESTCHILD', 'satcat', now(), %s)",
            (sat_stale, str(NORAD_STALE_OWNER), run_satcat),
        )

        # --- Section: SupGP cross-tag anomalies ---
        cur.execute(
            "INSERT INTO raw_supgp_status "
            "(norad_id, object_name, file_tag, flag, detail, ingest_run_id) "
            "VALUES (%s, 'ZZ TEST SUPGP OBJ', 'starlink', 'NO_MATCH', 'test anomaly', %s)",
            (NORAD_STATUS_DISAGREE, run_satcat),
        )

        # --- Section: match/merge stats ---
        cur.execute(
            "INSERT INTO merge_log (surviving_id, merged_id, rule_fired, score, details) "
            "VALUES (%s, %s, 'norad_exact', 1.0, '{}')",
            (sat_disagree, sat_decay),
        )
        cur.execute(
            "INSERT INTO source_assertion "
            "(satellite_id, source_key, attribute, value, source, observed_at, ingest_run_id) "
            "VALUES (NULL, 'zz-test-unmatched-1', 'name', 'ZZ Unmatched Object', 'ucs', now(), %s)",
            (run_satcat,),
        )

        # --- Section: coverage ---
        sat_rich = _insert_satellite(cur, NORAD_ON_ORBIT_RICH, "ZZ TEST ON ORBIT RICH")
        cur.execute(
            "INSERT INTO satellite_status_history "
            "(satellite_id, canonical_status, observed_at, source) VALUES (%s, 'ACTIVE', now(), 'satcat')",
            (sat_rich,),
        )
        cur.execute(
            "INSERT INTO operator (canonical_name, country, operator_class) "
            "VALUES ('ZZ Test Coverage Operator', 'US', 'commercial') RETURNING operator_id",
        )
        coverage_op_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO satellite_operator "
            "(satellite_id, operator_id, role, valid_from, valid_to, source) "
            "VALUES (%s, %s, 'owner', '2018-01-01', NULL, 'test')",
            (sat_rich, coverage_op_id),
        )
        cur.execute(
            "INSERT INTO satellite_identifier "
            "(satellite_id, id_type, id_value, source) VALUES "
            "(%s, 'norad', %s, 'satcat'), (%s, 'gcat_id', %s, 'gcat')",
            (sat_rich, str(NORAD_ON_ORBIT_RICH), sat_rich, f"J{NORAD_ON_ORBIT_RICH}"),
        )

        _insert_satellite(cur, NORAD_ON_ORBIT_BARE, "ZZ TEST ON ORBIT BARE")

        sat_decayed = _insert_satellite(
            cur, NORAD_DECAYED, "ZZ TEST DECAYED SAT", decay_date="2020-01-01"
        )
        cur.execute(
            "INSERT INTO satellite_status_history "
            "(satellite_id, canonical_status, observed_at, source) VALUES (%s, 'DECAYED', now(), 'satcat')",
            (sat_decayed,),
        )

    try:
        yield db_conn
    finally:
        db_conn.rollback()


def _row_for_norad(rows, norad_id):
    """Find the section row whose first column is the given synthetic norad id.

    Section queries are asserted directly (rather than the rendered report) so the tests are robust
    to a shared dev DB where thousands of real disagreements would push a high synthetic norad past
    the report's top-N example truncation.
    """
    return next((r for r in rows if r[0] == norad_id), None)


@pytest.mark.db
def test_report_generates_and_contains_planted_status_disagreement(seeded, tmp_path):
    # The report still renders end-to-end to a file...
    out_path = tmp_path / "dq_report.md"
    written = write_report(seeded, path=out_path)
    assert written == out_path
    assert out_path.exists()
    assert "## 1. Status disagreements" in out_path.read_text()

    # ...and the planted SATCAT-ACTIVE vs GCAT-DECAYED disagreement is present in the section data.
    with seeded.cursor() as cur:
        _, rows = _section_status_disagreements(cur)
    row = _row_for_norad(rows, NORAD_STATUS_DISAGREE)
    assert row is not None, "planted status disagreement missing from section"
    norad, name, satcat_status, gcat_status = row
    assert name == "ZZ TEST STATUS DISAGREE"
    assert satcat_status == "ACTIVE"
    assert gcat_status == "DECAYED"


@pytest.mark.db
def test_report_contains_decay_conflict_and_stale_owner_and_supgp(seeded):
    with seeded.cursor() as cur:
        _, decay_rows = _section_decay_date_conflicts(cur)
        _, stale_rows = _section_stale_post_ma_owners(cur)
        supgp_total, _, supgp_rows = _section_supgp_cross_tags(cur)

    decay = _row_for_norad(decay_rows, NORAD_DECAY_CONFLICT)
    assert decay is not None
    assert "2024-01-15" in decay[2] and "2024-02-20" in decay[2]

    stale = _row_for_norad(stale_rows, NORAD_STALE_OWNER)
    assert stale is not None
    assert "ZZ Test Child Co" in stale  # resolved_to_child
    assert "ZZ Test Parent Co" in stale  # should_be_parent

    supgp = next((r for r in supgp_rows if r[0] == NORAD_STATUS_DISAGREE), None)
    assert supgp is not None
    assert supgp[3] == "NO_MATCH"  # flag column


@pytest.mark.db
def test_report_is_byte_identical_across_runs_modulo_timestamp(seeded):
    """quality/report.py promises byte-identical output across runs except the generated-at header.
    Every 'latest per source' query has a deterministic tiebreaker, so two renders of the same
    seeded data must match once the timestamp line is stripped."""

    def _strip_ts(md):
        return "\n".join(line for line in md.splitlines() if not line.startswith("Generated at:"))

    first = generate_report(seeded)
    second = generate_report(seeded)
    assert _strip_ts(first) == _strip_ts(second)


@pytest.mark.db
def test_report_match_merge_stats_and_coverage_percentages_parse_as_numbers(seeded):
    content = generate_report(seeded)

    match_section = content.split("## 5. Match/merge stats")[1].split("## 6.")[0]
    assert "norad_exact" in match_section
    assert "gcat_id" in match_section or "norad" in match_section  # crosswalk id_type rows
    assert "ucs" in match_section  # unmatched-by-source row

    coverage_section = content.split("## 6. Coverage")[1]
    percentages = re.findall(r"\((\d+\.\d)%\)", coverage_section)
    assert len(percentages) == 3, f"expected 3 coverage percentages, got: {percentages}"
    for pct in percentages:
        value = float(pct)
        assert 0.0 <= value <= 100.0

    # The decayed satellite must not be counted as on-orbit.
    assert str(NORAD_DECAYED) not in coverage_section
