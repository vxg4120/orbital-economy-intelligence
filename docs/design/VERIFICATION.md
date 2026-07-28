# Verification protocol for attribution changes

Bus Benchmarks publishes numbers that people can cite, at URLs that people can bookmark, with a
frozen monthly archive that is meant to be immutable. That combination means an attribution
change is never only a code change. It can rewrite a published fact, break a link, or split a
historical series in two without anyone noticing.

This document is the protocol every change to `identity/bus.py`, `identity/reconcile.py`, or the
benchmark views goes through before it ships. It exists because two changes in July 2026 each
looked obviously safe and each turned out to be doing something other than what was claimed.

## The three things that can go wrong, in order of how quietly they fail

1. **A cohort merges into another.** Two slugs become one. Nothing 404s, no error is raised, a
   published fleet number simply becomes a different number. This is the worst failure because
   the only symptom is a wrong answer.
2. **A slug disappears or changes.** Every `/buses/{slug}` link to it breaks, the MCP `bus_detail`
   tool stops resolving it, and its rows in `bus_benchmark_snapshots` become unreachable, so the
   monthly series ends without ending.
3. **A metric moves.** The least dangerous, because it is visible and expected when the rule that
   computes it changes, but it still needs an explanation per cohort.

## Step 1: capture a baseline from a build, not from a database

`scripts/diff_published_buses.py --capture <path>` reads every cohort out of the benchmark views.

The capture must be taken **immediately after a full rebuild**, not from whatever state the
database happens to be in. Skipping this produced 55 phantom "changed" cohorts on 2026-07-28, all
of them behavior metrics that had not actually moved. The rebuild is what makes the comparison
mean something, because it holds the data constant while only the code varies:

```
.venv/bin/python scripts/build_bus.py                       # normalize the starting state
.venv/bin/python scripts/diff_published_buses.py --capture before.json
```

Rebuilds are deterministic. This was measured, not assumed: two consecutive rebuilds with no code
or data change produce zero differences across all 2,653 cohorts. If a rebuild ever does drift,
stop, because a scoreboard that cannot reproduce its own numbers has a bigger problem than
whatever change prompted the check.

## Step 2: make the change, rebuild, diff

```
.venv/bin/python scripts/build_bus.py
.venv/bin/python scripts/diff_published_buses.py --baseline before.json
```

The report separates vanished cohorts, appeared cohorts, cohorts whose `fleet_total` moved, and a
per-metric tally of everything else. **Every single line has to be explained before the change
ships.** An unexplained change is a bug that has not been found yet, not an acceptable cost.

Worked example, the uncertainty-marker fix of 2026-07-28. Predicted: one cohort, because exactly
one row in the catalog carried the defect. Observed:

```
manufacturer: 1 cohort changed, 0 vanished, 0 appeared
   raym: fleet_total 3 -> 4, decayed_count 3 -> 4, median_lifetime_years 15.22 -> 20.44
bus: 0 cohorts changed
```

Prediction matched observation exactly, so the change shipped.

## Step 3: gate it

```
.venv/bin/python scripts/diff_published_buses.py --baseline before.json --gate --allow raym
```

`--gate` exits non-zero if any cohort vanished or if any `fleet_total` moved without being named
in `--allow`. Naming a slug in `--allow` is a deliberate statement that the change to that
published number is intended, which is the point: the allowance is written down and reviewed
rather than discovered afterwards.

The tool refuses to produce a report at all if it finds two cohorts sharing a slug, because it
keys by slug and would otherwise collapse the pair and report the loss as no change. That guard
was added after it happened.

## Step 4: the invariants that live in the test suite

These run on every `pytest` and do not depend on anyone remembering the protocol.

| Test | Invariant | Why it exists |
| --- | --- | --- |
| `test_manufacturer_slug_is_unique` | No two cohorts share a slug, in either grouping | `RAYM?/GSFC` produced a code of `RAYM?` that slugified onto the real `RAYM`, giving one URL two meanings |
| `test_uncertainty_marker_never_survives_into_a_resolved_code` | No `?` inside any resolved org code | GCAT marks the individual code on a joint build, so stripping only a trailing marker leaves one embedded |
| `test_uncertainty_flag_covers_markers_anywhere_in_the_string` | `manufacturer_uncertain` is true whenever the raw value contains `?` | The flag is a dedup tiebreak, so under-flagging silently changes which catalog row wins |
| `test_attribution_agrees_with_piece_crosswalk` | Every attributed row sits on the satellite its COSPAR piece names | GCAT reassigned catalog ids for 67 of 73 Transporter-17 payloads in one release |
| `test_apex_fleet_fully_attributed` | Apex resolves to at least 5 | Known-data regression for the catalog-id reshuffle |
| `test_merge_repoints_every_table_that_references_satellite` | `merge()` handles every FK to `satellite`, checked against `information_schema` | Two migrations added references without updating `merge()`, and the graph build commits once, so one violation would roll back a whole night |
| `test_promotion_leaves_no_provisional_duplicate_of_an_anchored_satellite` | No COSPAR resolves to both a provisional and an anchored record | The duplicate-record state is what makes joins pick an arbitrary twin |

## Step 5: production verification after deploy

Deploying the code is not the same as applying it, because attribution lives in a built table.

```
ssh root@89.167.49.204 'cd /root/apps/space && git pull && cd deploy \
  && docker compose build oei-api && docker compose up -d oei-api \
  && docker compose exec -T oei-api python scripts/build_bus.py'
```

Then confirm against the live API rather than the local database, since that is what readers see.
Check the specific cohorts named in `--allow`, and check that the top of the leaderboard is
unchanged, which is the cheapest possible smoke test for an attribution change gone wide.

Finally, read `deploy/refresh.log` after the next nightly. The graph build runs in a single
transaction and the nightly script routes failures to a log rather than to a person, so a change
that only fails unattended will otherwise sit undetected. A useful signal that the identity
passes are behaving idempotently is that `merge_log` holds steady across consecutive runs rather
than growing.

## What this protocol deliberately does not cover

It says nothing about whether a change is a good idea, only about whether its effects are the
ones intended. The judgment about whether a rule matches how the industry actually attributes
spacecraft belongs in `docs/BUS_BENCHMARKS_METHODOLOGY.md`, versioned, with the reasoning in the
changelog where a reader can argue with it.
