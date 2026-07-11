# The Gold Set: how we measure identity-resolution quality

The identity graph makes millions of resolution decisions (which NORAD is which GCAT object, who
owns a satellite, what its status is). Aggregate coverage numbers ("% of payloads with a resolved
owner") tell you how *much* got resolved, not how *correctly*. The gold set is the ground-truth
program that answers the second question, honestly, per failure mode.

## Why a gold set (and why stratified)

A random sample of 200 satellites would be ~95% easy cases (one clean NORAD, one owner, one status)
and tell us almost nothing — the interesting errors hide in the hard tail. So instead we
**deterministically stratify** by known failure mode and hand-arbitrate the hard cases. The result
is a *lower bound* on whole-catalog accuracy (we deliberately picked the hard regions), which is the
honest and useful direction to be wrong in.

## The strata (and what each probes)

| stratum | what it probes | selection |
| --- | --- | --- |
| `ambiguous_cospar` | one COSPAR mapped to >1 satellite — legit cluster or bad merge? | all |
| `rideshare_orphan` | GCAT-only payloads with no NORAD (fresh rideshares) — distinct object? who operates? | all |
| `missed_join_candidate` | GCAT object name-similar (difflib ≥0.75) to a same-launch-window (±30d) SATCAT object the matcher did **not** link — a matcher-recall probe | top 30 by similarity |
| `owner_dispute` | SATCAT vs GCAT owner codes resolve to **different commercial operators** (not a hierarchy) | top 30, biggest objects first |
| `status_conflict` | SATCAT vs GCAT canonical operational-status disagreement | all |
| `decay_conflict` | largest parsed decay-date disagreements | top 20 by day-diff |
| `type_conflict` | SATCAT DEBRIS vs GCAT payload | ~25, spread across launch decades |
| `stale_owner` | post-M&A SCD2 ownership split (child until close, parent after) — right for THIS bird? | ~15, spread across deals |

Every case stores the full evidence packet: every identifier by source, every source assertion
(attribute/value/source/observed_at), launch/decay dates, orbital regime, and the graph's currently
resolved owner + status. Selection is in `scripts/build_gold_queue.py`; the table is `gold_case`
(migration `0007_gold.sql`).

## How to run it

```
make gold-queue     # (re)select cases into gold_case — idempotent; refreshes evidence, NEVER
                    #   overwrites a verdict. Safe to re-run any time the graph changes.
make review         # arbitrate unlabeled cases interactively (resumable, crash-safe).
                    #   Optional: .venv/bin/python scripts/review.py --type owner_dispute --limit 20
make gold-score     # write docs/reports/gold_eval.md from the verdicts so far (graceful at 0).
```

`review.py` shows each case as a side-by-side source table (conflicts highlighted), prints research
deep-links (resolver `/resolver/{id}`, GCAT object page, CelesTrak SATCAT, a Google query), and
takes a single-key verdict: `[c]orrect [i]ncorrect [p]artial [u]nresolvable [s]kip [n]ote [q]uit`.
`incorrect`/`partial` prompt for the corrected answer. Each verdict is written to the DB immediately
(crash-safe) and appended to `docs/gold/verdicts.jsonl` — **that committed file is the gold set**.
`data/gold/gold_cases.jsonl` (gitignored) is a full-fidelity export for resilience; restore with
`scripts/review.py --import-verdicts <file>`.

## How to quote the error rate honestly

- Scoring is `correct`=1.0, `partial`=0.5, `incorrect`=0.0 over **gradable** cases. `unresolvable`
  cases (truth undecidable even with the sources) are excluded from the denominator and reported
  separately — never silently folded into "correct".
- Always say **"hand-arbitrated hard cases"**, never "of all satellites". These accuracies are a
  lower bound sampled from suspected-hard regions, not a random-sample point estimate.
- Always name it **self-labeled** by the project owner (with the arbitration sources listed in the
  report). It is an internal quality instrument, not an independent third-party benchmark.
- `missed_join_candidate` is reported as a **matcher-recall proxy**, not a plain accuracy: of the
  near-miss pairs the matcher declined to link, how many were genuinely distinct vs true misses.

The generated report (`docs/reports/gold_eval.md`) states all of this inline and ends with a
"sentences you can now say" block wired to the real counts, so the honest framing travels with the
numbers.
