"""Tests for the gold evaluation program (Task P2 gold): migration, selection idempotence, the
review write path, and the scoring math.

DB-backed tests use a reserved synthetic key space (norad >= 940000001, case_type 'zz_test_*') and
run entirely inside a rolled-back transaction on the shared dev DB, so nothing is ever committed.
Pure-math tests need no DB.
"""

import json

import psycopg
import pytest

from scripts.build_gold_queue import upsert_case
from scripts.review import (
    append_jsonl,
    record_verdict,
    verdict_record,
)
from scripts.score_gold import _score, compute_scores

NORAD_BASE = 940000001
TEST_CASE_TYPE = "zz_test_stratum"


@pytest.fixture
def gold_txn(db_conn):
    """A db_conn whose transaction is always rolled back on teardown (never committed)."""
    try:
        yield db_conn
    finally:
        db_conn.rollback()


def _plant_case(cur, subject_ref, *, case_type=TEST_CASE_TYPE, verdict=None,
                corrected=None, notes=None, evidence='{"v": 1}', system_answer="old",
                question="old question"):
    cur.execute(
        "INSERT INTO gold_case (case_type, satellite_id, subject_ref, question, system_answer, "
        "evidence, verdict, corrected_answer, verdict_notes, labeled_at) "
        "VALUES (%s, NULL, %s, %s, %s, %s::jsonb, %s, %s, %s, "
        "        CASE WHEN %s::text IS NULL THEN NULL ELSE now() END) "
        "RETURNING case_id, labeled_at",
        (case_type, subject_ref, question, system_answer, evidence, verdict, corrected, notes,
         verdict),
    )
    return cur.fetchone()


# --------------------------------------------------------------------------------------------
# 1. Migration applied
# --------------------------------------------------------------------------------------------


@pytest.mark.db
def test_migration_gold_case_exists_with_expected_columns(gold_txn):
    with gold_txn.cursor() as cur:
        cur.execute("SELECT to_regclass('gold_case')")
        assert cur.fetchone()[0] == "gold_case"
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'gold_case'"
        )
        cols = {r[0] for r in cur.fetchall()}
    expected = {
        "case_id", "case_type", "satellite_id", "subject_ref", "question", "system_answer",
        "evidence", "verdict", "corrected_answer", "verdict_notes", "labeled_at", "created_at",
    }
    assert expected <= cols


@pytest.mark.db
def test_migration_verdict_check_constraint_rejects_bad_value(gold_txn):
    with gold_txn.cursor() as cur:
        cur.execute("SAVEPOINT sp")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO gold_case (case_type, subject_ref, question, system_answer, evidence, "
                "verdict) VALUES (%s, %s, 'q', 'a', '{}'::jsonb, 'bogus')",
                (TEST_CASE_TYPE, f"norad:{NORAD_BASE}"),
            )
        cur.execute("ROLLBACK TO SAVEPOINT sp")


@pytest.mark.db
def test_migration_unique_case_type_subject_ref(gold_txn):
    with gold_txn.cursor() as cur:
        _plant_case(cur, f"norad:{NORAD_BASE}")
        cur.execute("SAVEPOINT sp")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _plant_case(cur, f"norad:{NORAD_BASE}")
        cur.execute("ROLLBACK TO SAVEPOINT sp")


# --------------------------------------------------------------------------------------------
# 2. build_gold_queue idempotence preserves a planted verdict
# --------------------------------------------------------------------------------------------


@pytest.mark.db
def test_upsert_preserves_verdict_but_refreshes_evidence(gold_txn):
    subject = f"norad:{NORAD_BASE + 10}"
    with gold_txn.cursor() as cur:
        case_id, labeled_at = _plant_case(
            cur, subject, verdict="correct", corrected="hand answer", notes="planted",
            evidence='{"v": 1}', system_answer="old answer",
        )

    # Re-select the same case with fresh evidence/answer (what a re-run of the sampler does).
    upsert_case(gold_txn, {
        "case_type": TEST_CASE_TYPE,
        "satellite_id": None,
        "subject_ref": subject,
        "question": "new question",
        "system_answer": "new answer",
        "evidence": {"v": 2, "refreshed": True},
    })

    with gold_txn.cursor() as cur:
        cur.execute(
            "SELECT case_id, verdict, corrected_answer, verdict_notes, labeled_at, "
            "system_answer, question, evidence FROM gold_case "
            "WHERE case_type = %s AND subject_ref = %s",
            (TEST_CASE_TYPE, subject),
        )
        rows = cur.fetchall()

    assert len(rows) == 1, "upsert must not create a duplicate row"
    (got_id, verdict, corrected, notes, got_labeled, system_answer, question, evidence) = rows[0]
    # Human verdict columns are untouched...
    assert got_id == case_id
    assert verdict == "correct"
    assert corrected == "hand answer"
    assert notes == "planted"
    assert got_labeled == labeled_at
    # ...while the system's answer/evidence are refreshed.
    assert system_answer == "new answer"
    assert question == "new question"
    assert evidence == {"v": 2, "refreshed": True}


@pytest.mark.db
def test_upsert_inserts_when_absent(gold_txn):
    subject = f"norad:{NORAD_BASE + 11}"
    upsert_case(gold_txn, {
        "case_type": TEST_CASE_TYPE,
        "satellite_id": None,
        "subject_ref": subject,
        "question": "q",
        "system_answer": "a",
        "evidence": {"x": 1},
    })
    with gold_txn.cursor() as cur:
        cur.execute(
            "SELECT verdict, evidence FROM gold_case WHERE case_type = %s AND subject_ref = %s",
            (TEST_CASE_TYPE, subject),
        )
        verdict, evidence = cur.fetchone()
    assert verdict is None
    assert evidence == {"x": 1}


# --------------------------------------------------------------------------------------------
# 3. review.py verdict write path (factored, no interactive loop)
# --------------------------------------------------------------------------------------------


@pytest.mark.db
def test_record_verdict_writes_verdict_and_labeled_at(gold_txn):
    subject = f"norad:{NORAD_BASE + 20}"
    with gold_txn.cursor() as cur:
        case_id, _ = _plant_case(cur, subject)  # unlabeled

    rec = record_verdict(gold_txn, case_id, "incorrect", corrected_answer="fixed", notes="why")

    assert rec["case_type"] == TEST_CASE_TYPE
    assert rec["subject_ref"] == subject
    assert rec["verdict"] == "incorrect"
    assert rec["corrected_answer"] == "fixed"
    assert rec["verdict_notes"] == "why"
    assert rec["labeled_at"]  # iso timestamp string, non-empty

    with gold_txn.cursor() as cur:
        cur.execute(
            "SELECT verdict, corrected_answer, verdict_notes, labeled_at IS NOT NULL "
            "FROM gold_case WHERE case_id = %s",
            (case_id,),
        )
        verdict, corrected, notes, labeled = cur.fetchone()
    assert (verdict, corrected, notes, labeled) == ("incorrect", "fixed", "why", True)


@pytest.mark.db
def test_record_verdict_rejects_invalid_verdict(gold_txn):
    subject = f"norad:{NORAD_BASE + 21}"
    with gold_txn.cursor() as cur:
        case_id, _ = _plant_case(cur, subject)
    with pytest.raises(ValueError):
        record_verdict(gold_txn, case_id, "not-a-verdict")


def test_verdict_record_shape_and_jsonl_roundtrip(tmp_path):
    import datetime as dt

    rec = verdict_record(
        "owner_dispute", "norad:1", "partial", "corrected", "note",
        dt.datetime(2026, 7, 10, 12, 0, 0, tzinfo=dt.timezone.utc),
    )
    assert rec == {
        "case_type": "owner_dispute",
        "subject_ref": "norad:1",
        "verdict": "partial",
        "corrected_answer": "corrected",
        "verdict_notes": "note",
        "labeled_at": "2026-07-10T12:00:00+00:00",
    }
    path = tmp_path / "verdicts.jsonl"
    append_jsonl(path, rec)
    append_jsonl(path, rec)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == rec


# --------------------------------------------------------------------------------------------
# 4. Scoring math
# --------------------------------------------------------------------------------------------


def test_score_helper():
    assert _score(2, 1, 4) == 0.625            # (2 + 0.5) / 4
    assert _score(3, 0, 3) == 1.0
    assert _score(0, 0, 0) is None             # nothing gradable
    assert _score(0, 2, 4) == 0.25


@pytest.mark.db
def test_compute_scores_math_on_planted_stratum(gold_txn):
    # Plant a full verdict spread: 2 correct, 1 partial, 1 incorrect, 1 unresolvable.
    plants = [
        ("correct", 30), ("correct", 31), ("partial", 32), ("incorrect", 33),
        ("unresolvable", 34),
    ]
    with gold_txn.cursor() as cur:
        for verdict, offset in plants:
            _plant_case(cur, f"norad:{NORAD_BASE + offset}", verdict=verdict)

    scores = compute_scores(gold_txn)
    stratum = next(s for s in scores["strata"] if s["case_type"] == TEST_CASE_TYPE)

    assert stratum["labeled"] == 5
    assert stratum["correct"] == 2
    assert stratum["partial"] == 1
    assert stratum["incorrect"] == 1
    assert stratum["unresolvable"] == 1
    assert stratum["gradable"] == 4                # unresolvable excluded from denominator
    assert stratum["accuracy"] == 0.625            # (2 + 0.5*1) / 4

    # The planted stratum flows into the overall aggregate too.
    assert scores["overall"]["labeled"] >= 5
    assert scores["overall"]["accuracy"] is not None
