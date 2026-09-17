# Orbital API audit follow-up

Date: 2026-09-17. Isolated checkout: `wt-codex-space-followup-20260917`, branch
`codex/audit-followup-20260917`, base `ae3454e`.

## Scope and reproduced failures

Read the follow-up spec, FCC spec, local lessons, test conftest, and previous independent
Orbital audit before editing. Parent owns coordination, landing/Caddy, root spec/todo,
browser checks, and independent Codex review. This worker changed only operators,
conflicts, the existing warm cache, focused tests, and this report.

Before the source fix, isolated regressions reproduced:

- `/api/operators?sort=active` returned 422.
- Both status and stale-owner page requests executed the full expensive CTE twice:
  once for total and once for rows. The recording connection preserves old page
  slicing, so the regression fails on two executions, not incorrect mocked pagination.
- A background cold compute overlapped concurrent cache reads; multiple computes
  entered before the first completed. Empty results were used deliberately.
- Existing warm-read/failed-refresh behavior and retry after an initial failure
  already passed; these are preserved contracts.

## Changes

- `active` is an allowlisted sort expression, `fleet_active DESC, operator_id`.
  Unsupported and SQL-like sort input still returns 422 before SQL execution.
- Status and stale-owner conflicts register complete ordered row payloads with the
  same warm-cache registry used by decay conflicts. Page rows are sliced from one
  payload and totals/count helpers take its length. No separate count CTE is run.
  The outer SELECT explicitly orders by NORAD (nulls last), then satellite ID.
- A separate refresh lock serializes expensive computations. Cold reads recheck
  whether a payload was populated while waiting; valid empty lists are cached.
  The startup loop reuses an inline-populated first payload. Warm reads retain a
  short separate payload lock and do not wait for a slow or failed refresh.
- Refresh interval 900 seconds, retry interval 60 seconds, stale-value retention,
  request-connection behavior when disabled, and public API payloads are preserved.

## Verification

The existing source venv was used read-only for Python dependencies:
`/Users/vgupta/Development/repos/space/.venv/bin/python`.

1. With `DATABASE_URL=intentionally-invalid-no-database`:
   `python -m pytest tests/test_api_orbital_followup.py -q -m 'not db'`
   passed **12**, deselected **2**. Covers route shape, invalid sort rejection,
   one query per uncached page, row/count snapshot consistency, refresh changes,
   empty corpora, out-of-range offsets, concurrent cold reads, nonblocking stale
   reads, failed refresh recovery, startup reuse, and 900/60-second timer behavior.
2. Disposable local PostgreSQL 14.13, unique port **59139**, unique data directory
   `/var/folders/8l/k8h8vpt11x972r4nhgmvyd100000gn/T/orbital-followup-pg-qhkjmbu2`.
   Explicit test DSN was `postgresql://vgupta@127.0.0.1:59139/postgres?connect_timeout=2`.
   `python -m pytest tests/test_api_orbital_followup.py -q` passed **14**.
   Actual SQL tests create only transaction-local TEMP tables. They prove Active
   sorting across pagination with tied active counts and a zero-fleet operator;
   and status/stale-owner ordering, provenance, UNKNOWN/agreement exclusion,
   page/count equality, and null-NORAD ordering. Server stopped in a finally block.
3. With the same deliberately invalid DSN:
   `python -m pytest tests/test_api_orbital_followup.py tests/test_api_conflicts.py
   tests/test_api_operators.py tests/test_api_stats.py tests/test_marker_hygiene.py -q`
   passed **14**, skipped **19** database-dependent existing tests. Those skips
   are not a production-corpus or full-suite pass.
4. `python -m ruff check api/cache.py api/routers/operators.py
   api/routers/conflicts.py tests/test_api_orbital_followup.py` passed.
   `git diff --check` passed.

Only warning: the installed Starlette TestClient warns that its current httpx
integration is deprecated. No dependencies were changed.

## Limits and remaining work

- No production database or production commands, deployment, harvest, external
  messages, or paid API calls. Local tests do not establish deployment or live latency.
- Complete conflict results now reside in process memory, as decay already does.
  A genuinely cold start still has to run the underlying query once; this change
  avoids repeated queries and does not claim to optimize the CTE itself.
- `/api/stats` separately caches its whole response. Its already-cached headline
  can therefore lag a newly refreshed conflict list by its existing refresh cadence.
  The count helpers now read the same row caches; no stronger cross-endpoint
  snapshot guarantee is claimed. Parent notified for review/scope decision.
- Parent will conduct local browser interaction checks and independent read-only
  Codex verification of the integrated changes before release consideration.
