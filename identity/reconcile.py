"""Promotion of provisional satellite records onto their permanently anchored twins.

A freshly launched rideshare payload enters the graph twice. GCAT publishes it immediately under
its real name with a provisional catalog id and no NORAD id ('MISR-D-1'), while Space-Track
publishes the tracked object with a NORAD id and a placeholder name ('TRANSPORTER-17 OBJECT AJ').
Neither deterministic pass can link them, because the NORAD pass needs a NORAD id the GCAT row
does not have, and the probabilistic name matcher scores those two strings near zero. The result
is two satellite records for one spacecraft, which is what makes downstream joins pick whichever
record happens to carry the key they matched on.

This module folds the provisional record into the anchored one. The anchored record survives
because its identity is pinned by a key that does not move: Space-Track's norad to object_id
mapping has never changed for a catalogued object, whereas GCAT reassigns both its own catalog
ids and, for a period after launch, which object it believes occupies a given COSPAR piece.

Pairs are proposed on (COSPAR identifier, launch date) and must clear a name gate before merging.
The inverse operation, splitting a conflated record, is never performed automatically: getting a
merge wrong is a data quality problem, and automating the split turns it into a data loss problem.

No commit here: the caller owns the transaction (same contract as the rest of identity/).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from identity import merge as merge_mod
from identity.normalize import norm_name

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REVIEW_CSV = REPO_ROOT / "data" / "review" / "promotion_review.csv"

RULE = "promotion_cospar_launch"

# Space-Track names a fresh rideshare object after the very piece letter that is still moving
# ('TRANSPORTER-17 OBJECT AJ'), so the name carries no independent identity signal. Such a name
# is neither evidence of agreement nor of disagreement, and the gate passes on the COSPAR and
# launch-date match alone.
_PLACEHOLDER_NAME = re.compile(r"\bOBJECT\b", re.IGNORECASE)

# Provisional satellites sharing a COSPAR identifier and launch date with exactly ONE anchored
# satellite. The HAVING clause is the safety rail: a provisional record that could belong to more
# than one anchored record is left alone rather than guessed at.
_CANDIDATES_SQL = """
SELECT p.satellite_id           AS provisional_id,
       min(n.satellite_id)      AS anchored_id,
       max(p.canonical_name)    AS provisional_name,
       max(n.canonical_name)    AS anchored_name,
       min(pi.id_value)         AS cospar
FROM satellite p
JOIN satellite_identifier pi
  ON pi.satellite_id = p.satellite_id AND pi.id_type = 'cospar' AND pi.valid_to IS NULL
JOIN satellite_identifier ni
  ON ni.id_type = 'cospar' AND ni.id_value = pi.id_value AND ni.valid_to IS NULL
JOIN satellite n
  ON n.satellite_id = ni.satellite_id
WHERE p.norad_id IS NULL
  AND n.norad_id IS NOT NULL
  AND p.satellite_id <> n.satellite_id
  AND p.launch_date IS NOT DISTINCT FROM n.launch_date
GROUP BY p.satellite_id
HAVING count(DISTINCT n.satellite_id) = 1
ORDER BY p.satellite_id
"""


def name_gate(provisional_name: str | None, anchored_name: str | None) -> bool:
    """Whether two names are compatible enough to merge on a COSPAR and launch-date match.

    Passes when the anchored name is a catalog placeholder (no identity signal to contradict), or
    when both names normalize to the same key. Anything else is a real disagreement and declines.
    """
    if anchored_name and _PLACEHOLDER_NAME.search(anchored_name):
        return True
    return bool(norm_name(provisional_name)) and norm_name(provisional_name) == norm_name(
        anchored_name
    )


def promote(conn, review_csv: Path | None = None) -> dict:
    """Fold provisional satellite records into their anchored twins. Returns summary stats.

    Idempotent: once a pair is merged the provisional record no longer exists, so a second run
    over unchanged data selects nothing.
    """
    review_csv = DEFAULT_REVIEW_CSV if review_csv is None else Path(review_csv)
    with conn.cursor() as cur:
        cur.execute(_CANDIDATES_SQL)
        columns = [d.name for d in cur.description]
        candidates = [dict(zip(columns, row)) for row in cur.fetchall()]

    merged, declined = 0, []
    for c in candidates:
        if not name_gate(c["provisional_name"], c["anchored_name"]):
            declined.append(c)
            continue
        merge_mod.merge(
            conn,
            surviving_id=c["anchored_id"],
            merged_id=c["provisional_id"],
            rule=RULE,
            score=1.0,
            details={
                "cospar": c["cospar"],
                "provisional_name": c["provisional_name"],
                "anchored_name": c["anchored_name"],
            },
        )
        merged += 1

    if declined:
        _write_review(review_csv, declined)

    return {
        "candidates": len(candidates),
        "promoted": merged,
        "declined": len(declined),
        "review_csv": str(review_csv) if declined else None,
    }


def _write_review(path: Path, rows: list[dict]) -> None:
    """Append declined pairs for human review; a declined pair is a name conflict worth reading."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not exists:
            writer.writerow(
                ["provisional_id", "anchored_id", "cospar", "provisional_name", "anchored_name"]
            )
        for r in rows:
            writer.writerow(
                [
                    r["provisional_id"],
                    r["anchored_id"],
                    r["cospar"],
                    r["provisional_name"],
                    r["anchored_name"],
                ]
            )
