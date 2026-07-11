# Gold-Set Evaluation: identity-resolution accuracy by failure mode
Generated at: 2026-07-11 02:24:22 UTC

## Methodology (read this before quoting a number)

This is a **self-labeled** gold set: the cases are chosen by a deterministic, stratified sampler (`scripts/build_gold_queue.py`) that targets known hard failure modes, and each verdict is hand-arbitrated by the project owner via `scripts/review.py`. It is therefore an honest internal quality instrument, **not** an independent third-party benchmark. Arbitration draws on: the resolver deep-view (`/resolver/{id}`), GCAT object pages (planet4589.org, CC-BY Jonathan McDowell), CelesTrak SATCAT records, and public record (company filings, launch press). Because cases are deliberately sampled from suspected-hard regions of the catalog, these accuracies are a **lower bound** on whole-catalog accuracy, not a random-sample estimate.

Scoring: `correct` = 1.0, `partial` = 0.5, `incorrect` = 0.0. `unresolvable` cases (truth undecidable even with the sources) are **excluded from the denominator** (`gradable`), and counted separately so the exclusion is visible.

## Status: no cases labeled yet

The queue holds **246** selected cases across 7 strata, 0 labeled. Run `make review` to begin arbitration; re-run `make gold-score` to populate the tables below.

### Selected cases per stratum

| stratum | total | labeled |
| --- | --- | --- |
| ambiguous_cospar | 48 | 0 |
| rideshare_orphan | 73 | 0 |
| owner_dispute | 30 | 0 |
| status_conflict | 35 | 0 |
| decay_conflict | 20 | 0 |
| type_conflict | 25 | 0 |
| stale_owner | 15 | 0 |
