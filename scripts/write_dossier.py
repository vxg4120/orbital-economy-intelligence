"""Upsert an AI research dossier for a gold case.

Usage: python scripts/write_dossier.py path/to/dossier.json   (or '-' for stdin)

JSON shape:
{ "case_id": 1, "recommended_verdict": "incorrect", "recommended_answer": "...",
  "confidence": "high", "summary": "...", "evidence": [{"claim": "...",
  "source_name": "...", "url": "..."}], "caveats": "...", "agent_model": "..." }
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from common.db import get_conn  # noqa: E402

UPSERT = """
INSERT INTO gold_dossier (case_id, recommended_verdict, recommended_answer, confidence,
                          summary, evidence, caveats, agent_model, researched_at)
VALUES (%(case_id)s, %(recommended_verdict)s, %(recommended_answer)s, %(confidence)s,
        %(summary)s, %(evidence)s, %(caveats)s, %(agent_model)s, now())
ON CONFLICT (case_id) DO UPDATE SET
    recommended_verdict = EXCLUDED.recommended_verdict,
    recommended_answer  = EXCLUDED.recommended_answer,
    confidence          = EXCLUDED.confidence,
    summary             = EXCLUDED.summary,
    evidence            = EXCLUDED.evidence,
    caveats             = EXCLUDED.caveats,
    agent_model         = EXCLUDED.agent_model,
    researched_at       = now()
"""


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    raw = sys.stdin.read() if src == "-" else Path(src).read_text()
    d = json.loads(raw)
    for k in ("case_id", "recommended_verdict", "confidence", "summary"):
        if not d.get(k):
            raise SystemExit(f"missing required field: {k}")
    payload = {
        "case_id": d["case_id"],
        "recommended_verdict": d["recommended_verdict"],
        "recommended_answer": d.get("recommended_answer"),
        "confidence": d["confidence"],
        "summary": d["summary"],
        "evidence": json.dumps(d.get("evidence", [])),
        "caveats": d.get("caveats"),
        "agent_model": d.get("agent_model"),
    }
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(UPSERT, payload)
    conn.commit()
    print(f"dossier written for case {d['case_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
