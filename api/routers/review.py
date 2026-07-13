"""The Review area API: a visual arbitration workbench over the gold_case corpus.

Read side (read-only connection, api.deps.get_db):
  * GET /api/review/stats            per-stratum + overall label counts and accuracy-so-far
  * GET /api/review/cases            paginated case list, filterable by type / label state
  * GET /api/review/cases/{id}       one full case incl. the evidence JSONB
  * GET /api/review/next             the next unlabeled case id in stable order (wraps)

Write side (a SEPARATE writable connection, api.deps.get_write_db):
  * POST /api/review/cases/{id}/verdict   record a verdict; guarded by the X-Review-Token header.

The write goes through common.gold_verdicts -- the exact same helper the review CLI uses -- so the
DB row and the committed docs/gold/verdicts.jsonl file (which IS the gold set) stay in lockstep no
matter which surface recorded the verdict. Ordering everywhere is the stable (case_type, case_id)
so list position, next-case, and the CLI all agree.
"""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_db, get_write_db
from common.gold_verdicts import (
    VERDICT_VALUES,
    VERDICTS_PATH,
    append_jsonl,
    record_verdict,
)

router = APIRouter(prefix="/review", tags=["review"])

# Presentation order for strata; unknown/synthetic types sort after, alphabetically.
STRATUM_ORDER = [
    "ambiguous_cospar",
    "rideshare_orphan",
    "missed_join_candidate",
    "owner_dispute",
    "status_conflict",
    "decay_conflict",
    "type_conflict",
    "stale_owner",
]


def _accuracy(correct: int, partial: int, gradable: int) -> float | None:
    """(correct + 0.5*partial) / gradable, or None when nothing is gradable. Mirrors score_gold."""
    if gradable <= 0:
        return None
    return (correct + 0.5 * partial) / gradable


@router.get("/stats")
def review_stats(db=Depends(get_db)):
    """Per-stratum and overall label counts + an honest accuracy-so-far over gradable cases.

    ``accuracy_so_far`` scores correct=1.0 / partial=0.5 / incorrect=0.0 and EXCLUDES unresolvable
    cases from the denominator, exactly as scripts/score_gold.py does, so this chip never disagrees
    with the generated gold_eval.md report.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT case_type, verdict, count(*) AS n FROM gold_case GROUP BY case_type, verdict"
        )
        rows = cur.fetchall()

    tally: dict[str, dict] = {}
    for r in rows:
        tally.setdefault(r["case_type"], {})[r["verdict"]] = r["n"]

    ordered = [t for t in STRATUM_ORDER if t in tally]
    ordered += sorted(set(tally) - set(STRATUM_ORDER))

    agg = {v: 0 for v in VERDICT_VALUES}
    agg_total = 0
    strata = []
    for case_type in ordered:
        counts = tally[case_type]
        total = sum(counts.values())  # includes the None (unlabeled) bucket
        verdicts = {v: counts.get(v, 0) for v in VERDICT_VALUES}
        labeled = sum(verdicts.values())
        strata.append({"case_type": case_type, "total": total, "labeled": labeled, **verdicts})
        for v in VERDICT_VALUES:
            agg[v] += verdicts[v]
        agg_total += total

    labeled = sum(agg.values())
    gradable = labeled - agg["unresolvable"]
    overall = {"total": agg_total, "labeled": labeled, **agg}
    return {
        "strata": strata,
        "overall": overall,
        "accuracy_so_far": _accuracy(agg["correct"], agg["partial"], gradable),
    }


_ONLY_FILTER = {
    "unlabeled": "verdict IS NULL",
    "labeled": "verdict IS NOT NULL",
    "all": None,
}


@router.get("/cases")
def review_cases(
    db=Depends(get_db),
    type: str | None = Query(None, description="filter to one case_type / stratum"),
    only: str = Query("unlabeled", pattern="^(unlabeled|labeled|all)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Paginated case list in stable (case_type, case_id) order. Returns {rows, total}."""
    where: list[str] = []
    params: list = []
    if type:
        where.append("case_type = %s")
        params.append(type)
    label_clause = _ONLY_FILTER[only]
    if label_clause:
        where.append(label_clause)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM gold_case{clause}", params)
        total = cur.fetchone()["total"]
        cur.execute(
            "SELECT case_id, case_type, subject_ref, question, verdict, labeled_at "
            f"FROM gold_case{clause} ORDER BY case_type, case_id LIMIT %s OFFSET %s",
            [*params, limit, offset],
        )
        rows = cur.fetchall()
    return {"rows": rows, "total": total}


@router.get("/next")
def review_next(
    db=Depends(get_db),
    type: str | None = Query(None),
    after_case_id: int | None = Query(None),
):
    """The next unlabeled case id after ``after_case_id`` in (case_type, case_id) order; wraps to
    the first unlabeled when it falls off the end, and returns null when none remain."""
    filt = "verdict IS NULL"
    params: list = []
    if type:
        filt += " AND case_type = %s"
        params.append(type)

    with db.cursor() as cur:
        ref = None
        if after_case_id is not None:
            cur.execute(
                "SELECT case_type, case_id FROM gold_case WHERE case_id = %s", (after_case_id,)
            )
            ref = cur.fetchone()
        if ref is not None:
            cur.execute(
                f"SELECT case_id FROM gold_case WHERE {filt} AND (case_type, case_id) > (%s, %s) "
                "ORDER BY case_type, case_id LIMIT 1",
                [*params, ref["case_type"], ref["case_id"]],
            )
            nxt = cur.fetchone()
            if nxt is not None:
                return {"next_case_id": nxt["case_id"]}
        # No reference (or ran off the end): wrap to the first unlabeled case in the filter.
        cur.execute(
            f"SELECT case_id FROM gold_case WHERE {filt} ORDER BY case_type, case_id LIMIT 1",
            params,
        )
        nxt = cur.fetchone()
    return {"next_case_id": nxt["case_id"] if nxt is not None else None}


@router.get("/cases/{case_id}")
def review_case(case_id: int, db=Depends(get_db)):
    """One full case: identity + question + system answer + the raw evidence JSONB + any verdict."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT case_id, case_type, satellite_id, subject_ref, question, system_answer, "
            "evidence, verdict, corrected_answer, verdict_notes, labeled_at "
            "FROM gold_case WHERE case_id = %s",
            (case_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    return row


class VerdictBody(BaseModel):
    verdict: str
    corrected_answer: str | None = None
    notes: str | None = None
    overwrite: bool = False


def _require_token(token: str | None) -> None:
    """Constant-time check of the X-Review-Token header against the REVIEW_TOKEN env var.

    Per the contract this route requires a header matching REVIEW_TOKEN; anything else is a 401
    (a server with no token configured cannot be matched, so it too rejects with 401).
    """
    expected = os.environ.get("REVIEW_TOKEN")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing review token")


@router.post("/cases/{case_id}/verdict")
def submit_verdict(
    case_id: int,
    body: VerdictBody,
    db=Depends(get_write_db),
    x_review_token: str | None = Header(default=None),
):
    """Record a verdict for one case. Requires X-Review-Token; 409 if already labeled unless
    ``overwrite`` is set. Writes via the shared helper and appends to verdicts.jsonl, then commits."""
    _require_token(x_review_token)
    if body.verdict not in VERDICT_VALUES:
        raise HTTPException(status_code=422, detail=f"invalid verdict: {body.verdict!r}")

    with db.cursor() as cur:
        cur.execute("SELECT labeled_at FROM gold_case WHERE case_id = %s", (case_id,))
        existing = cur.fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="case not found")
    if existing["labeled_at"] is not None and not body.overwrite:
        raise HTTPException(
            status_code=409, detail="case already labeled; resubmit with overwrite=true to relabel"
        )

    rec = record_verdict(db, case_id, body.verdict, body.corrected_answer, body.notes)
    db.commit()  # durable before we touch the committed gold file
    append_jsonl(VERDICTS_PATH, rec)
    return {"ok": True, "verdict": rec}
