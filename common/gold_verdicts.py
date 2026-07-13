"""The single gold-verdict write path: DB update + docs/gold/verdicts.jsonl append.

Factored out of scripts/review.py so the arbitration CLI and the review API
(api/routers/review.py) record verdicts through *exactly one* code path. The committed
``docs/gold/verdicts.jsonl`` file IS the gold set, so there must be a single writer that keeps the
DB row and that file in lockstep.

Transaction-agnostic: the caller owns the commit. The CLI commits immediately (crash-safe before
the next case is shown); the API commits once per request. Neither this module nor its helpers open
or close a connection, so it composes with the CLI's shared connection and the API's separate
writable connection alike.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VERDICTS_PATH = REPO_ROOT / "docs" / "gold" / "verdicts.jsonl"  # committed: the gold set

# The only verdicts the CHECK constraint on gold_case.verdict accepts.
VERDICT_VALUES = ("correct", "incorrect", "partial", "unresolvable")


def record_verdict(conn, case_id, verdict, corrected_answer=None, notes=None) -> dict:
    """Write a verdict + labeled_at to gold_case. Transaction-agnostic: the caller commits.

    Returns the canonical verdict record (the dict shape appended to verdicts.jsonl). Raises
    ValueError for an out-of-vocabulary verdict and LookupError when case_id does not exist.
    """
    if verdict not in VERDICT_VALUES:
        raise ValueError(f"invalid verdict: {verdict!r}")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gold_case SET verdict = %s, corrected_answer = %s, verdict_notes = %s, "
            "labeled_at = now() WHERE case_id = %s "
            "RETURNING case_type, subject_ref, verdict, corrected_answer, verdict_notes, labeled_at",
            (verdict, corrected_answer, notes, case_id),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"case_id {case_id} not found")
    # Support both tuple rows (CLI's plain connection) and dict rows (the API's dict_row conn).
    if isinstance(row, dict):
        return verdict_record(
            row["case_type"], row["subject_ref"], row["verdict"], row["corrected_answer"],
            row["verdict_notes"], row["labeled_at"],
        )
    return verdict_record(*row)


def verdict_record(case_type, subject_ref, verdict, corrected_answer, verdict_notes, labeled_at):
    """The canonical dict shape written to verdicts.jsonl (the committed gold set)."""
    return {
        "case_type": case_type,
        "subject_ref": subject_ref,
        "verdict": verdict,
        "corrected_answer": corrected_answer,
        "verdict_notes": verdict_notes,
        "labeled_at": labeled_at.isoformat() if isinstance(labeled_at, dt.datetime) else labeled_at,
    }


def append_jsonl(path: pathlib.Path, obj: dict) -> None:
    """Append one JSON object as a line to ``path`` (creating parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")
