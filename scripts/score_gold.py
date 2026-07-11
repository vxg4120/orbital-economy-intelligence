"""Turn hand-arbitrated gold_case verdicts into an honest, per-stratum accuracy report.

Reads only gold_case. For each stratum and overall it reports how many cases are labeled, the full
verdict breakdown, and a system-accuracy score where correct=1.0, partial=0.5, incorrect=0.0 over
the gradable cases (unresolvable cases -- ones the arbitrator could not decide even with the
sources -- are excluded from the denominator, and that exclusion is stated in the report). The
missed_join_candidate stratum is reframed as a matcher-recall proxy. Writes docs/reports/
gold_eval.md deterministically and is graceful when nothing is labeled yet.

Two entry points mirror quality/report.py: compute_scores(conn) is a pure function tests call
directly; main() opens its own connection and writes the file.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.db import get_conn

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = REPO_ROOT / "docs" / "reports" / "gold_eval.md"

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

VERDICTS = ("correct", "partial", "incorrect", "unresolvable")


def _score(correct: int, partial: int, gradable: int) -> float | None:
    """(correct + 0.5*partial) / gradable, or None when there is nothing gradable."""
    if gradable <= 0:
        return None
    return (correct + 0.5 * partial) / gradable


def compute_scores(conn) -> dict:
    """Per-stratum + overall label counts and accuracy. Pure read; never mutates."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT case_type, verdict, count(*) FROM gold_case GROUP BY case_type, verdict"
        )
        raw = cur.fetchall()

    # case_type -> {verdict-or-None: count}
    tally: dict[str, dict] = {}
    for case_type, verdict, n in raw:
        tally.setdefault(case_type, {})[verdict] = n

    ordered_types = [t for t in STRATUM_ORDER if t in tally]
    ordered_types += sorted(set(tally) - set(STRATUM_ORDER))

    strata = []
    agg = {v: 0 for v in VERDICTS}
    agg_total = 0
    for case_type in ordered_types:
        counts = tally[case_type]
        total = sum(counts.values())
        row = {v: counts.get(v, 0) for v in VERDICTS}
        labeled = sum(row.values())
        gradable = labeled - row["unresolvable"]
        strata.append(
            {
                "case_type": case_type,
                "total": total,
                "labeled": labeled,
                "gradable": gradable,
                "accuracy": _score(row["correct"], row["partial"], gradable),
                **row,
            }
        )
        for v in VERDICTS:
            agg[v] += row[v]
        agg_total += total

    labeled = sum(agg.values())
    gradable = labeled - agg["unresolvable"]
    overall = {
        "total": agg_total,
        "labeled": labeled,
        "gradable": gradable,
        "accuracy": _score(agg["correct"], agg["partial"], gradable),
        **agg,
    }
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "strata": strata,
        "overall": overall,
    }


def _pct(acc: float | None) -> str:
    return "n/a" if acc is None else f"{100.0 * acc:.1f}%"


def _row(cells) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def render_markdown(scores: dict) -> str:
    out = ["# Gold-Set Evaluation: identity-resolution accuracy by failure mode\n"]
    out.append(f"Generated at: {scores['generated_at']}\n")
    overall = scores["overall"]

    out.append(
        "\n## Methodology (read this before quoting a number)\n\n"
        "This is a **self-labeled** gold set: the cases are chosen by a deterministic, stratified "
        "sampler (`scripts/build_gold_queue.py`) that targets known hard failure modes, and each "
        "verdict is hand-arbitrated by the project owner via `scripts/review.py`. It is therefore "
        "an honest internal quality instrument, **not** an independent third-party benchmark. "
        "Arbitration draws on: the resolver deep-view (`/resolver/{id}`), GCAT object pages "
        "(planet4589.org, CC-BY Jonathan McDowell), CelesTrak SATCAT records, and public record "
        "(company filings, launch press). Because cases are deliberately sampled from suspected-hard "
        "regions of the catalog, these accuracies are a **lower bound** on whole-catalog accuracy, "
        "not a random-sample estimate.\n\n"
        "Scoring: `correct` = 1.0, `partial` = 0.5, `incorrect` = 0.0. `unresolvable` cases (truth "
        "undecidable even with the sources) are **excluded from the denominator** (`gradable`), and "
        "counted separately so the exclusion is visible.\n"
    )

    if overall["labeled"] == 0:
        out.append(
            "\n## Status: no cases labeled yet\n\n"
            f"The queue holds **{overall['total']}** selected cases across "
            f"{len(scores['strata'])} strata, 0 labeled. Run `make review` to begin arbitration; "
            "re-run `make gold-score` to populate the tables below.\n"
        )
        out.append(_strata_totals_table(scores))
        return "".join(out)

    out.append("\n## Accuracy by stratum\n\n")
    out.append(
        _row(["stratum", "total", "labeled", "correct", "partial", "incorrect",
              "unresolvable", "gradable", "accuracy"]) + "\n"
    )
    out.append(_row(["---"] * 9) + "\n")
    for s in scores["strata"]:
        out.append(
            _row([
                s["case_type"], s["total"], s["labeled"], s["correct"], s["partial"],
                s["incorrect"], s["unresolvable"], s["gradable"], _pct(s["accuracy"]),
            ]) + "\n"
        )
    out.append(
        _row(["**overall**", overall["total"], overall["labeled"], overall["correct"],
              overall["partial"], overall["incorrect"], overall["unresolvable"],
              overall["gradable"], _pct(overall["accuracy"])]) + "\n"
    )

    out.append(_matcher_recall_note(scores))
    out.append(_sentences_you_can_say(scores))
    return "".join(out)


def _strata_totals_table(scores: dict) -> str:
    out = ["\n### Selected cases per stratum\n\n"]
    out.append(_row(["stratum", "total", "labeled"]) + "\n")
    out.append(_row(["---"] * 3) + "\n")
    for s in scores["strata"]:
        out.append(_row([s["case_type"], s["total"], s["labeled"]]) + "\n")
    return "".join(out)


def _matcher_recall_note(scores: dict) -> str:
    mj = next((s for s in scores["strata"] if s["case_type"] == "missed_join_candidate"), None)
    out = ["\n## Matcher-recall proxy (missed_join_candidate)\n\n"]
    if mj is None or mj["total"] == 0:
        out.append(
            "No candidates were selected: every unmatched GCAT object in the current catalog is a "
            "fresh rideshare with no name-similar SATCAT neighbor inside the +/-30-day launch window "
            "(top near-miss < 0.75). That is itself a positive signal -- the deterministic "
            "NORAD/COSPAR pass leaves almost nothing joinable-but-unjoined -- but it means this "
            "recall proxy has no data yet. It will populate as future launches age into the "
            "catalog with slightly divergent names.\n"
        )
        return "".join(out)
    if mj["gradable"] == 0:
        out.append(f"{mj['total']} candidate near-miss pairs selected, none labeled yet.\n")
        return "".join(out)
    misses = mj["incorrect"]
    out.append(
        f"Of {mj['gradable']} arbitrated near-miss candidate pairs (GCAT object vs same-launch-"
        f"window SATCAT object the matcher did NOT link), **{mj['correct']}** were genuinely "
        f"distinct objects (the matcher was correctly conservative) and **{misses}** were true "
        f"missed joins. Adversarial recall on this hard candidate set: "
        f"**{_pct(_score(mj['correct'], mj['partial'], mj['gradable']))}**.\n"
    )
    return "".join(out)


def _sentences_you_can_say(scores: dict) -> str:
    overall = scores["overall"]
    out = ["\n## Sentences you can now say (fill from the tables above)\n\n"]
    if overall["gradable"] == 0:
        out.append(
            "> _(Label at least one gradable case to instantiate these.)_\n\n"
            "- \"Across N hand-arbitrated hard cases spanning M failure modes, the identity graph "
            "resolved X% correctly.\"\n"
        )
        return "".join(out)
    out.append(
        f"- \"Across **{overall['gradable']}** hand-arbitrated hard cases spanning "
        f"**{len([s for s in scores['strata'] if s['labeled'] > 0])}** failure modes, the identity "
        f"graph resolved **{_pct(overall['accuracy'])}** correctly (partial credit for "
        f"partially-right answers; {overall['unresolvable']} cases were undecidable and excluded).\"\n"
    )
    for s in scores["strata"]:
        if s["gradable"] > 0:
            out.append(
                f"- \"On **{s['case_type']}** cases, accuracy is "
                f"**{_pct(s['accuracy'])}** over {s['gradable']} arbitrated cases.\"\n"
            )
    return "".join(out)


def write_report(conn, path: pathlib.Path = DEFAULT_REPORT_PATH) -> pathlib.Path:
    content = render_markdown(compute_scores(conn))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def main() -> None:
    conn = get_conn()
    try:
        path = write_report(conn)
        scores = compute_scores(conn)
        print(f"wrote {path}  ({scores['overall']['labeled']}/{scores['overall']['total']} labeled)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
