# Spec: Close independently reproduced site audit gaps

**Status:** active
**Owner:** Vib
**Repos touched:** space; Exo has its own isolated spec
**Last updated:** 2026-09-17

## Goal
Fix the live blank landing404 and unsupported Operators Active sort, eliminate
duplicate cold count/page conflict queries, and verify the changes with realistic
isolated tests. Base: released space `ae3454e`, including Vib-confirmed first QA2014.

## Architecture decisions
- 2026-09-17 — Preserve the existing API, warm-cache architecture, raw identities,
  and release branch. Work in isolated `codex/audit-followup-20260917`.
- 2026-09-17 — Reproduce server404 handling using a real local Caddy, since a200
  for404.html and a valid Caddyfile do not prove unknown routes serve that body.

## Constraints
- No production mutation, deployment, AWS/kubectl, production DB access/writes,
  harvests, paid APIs, or email. Local disposable test databases are allowed.
- Do not modify Claude's release worktrees or share a test DB with another session.
- Keep public routes, HTTP status semantics, count/page consistency, source
  provenance, cache TTL/error behavior and existing FCC/bus spec constraints.
- Do not widen supported sort expressions through untrusted string interpolation.
- Preserve first QA2014 and the separately listed employer dates.

## Interfaces & ownership
- Root: landing `deploy/caddy/Caddyfile`, landing robustness/mobile copy as confirmed,
  local HTTP/browser verification, this spec/todo, integration/review.
- Orbital worker: operators/conflicts routers, cache registration, focused tests,
  `tasks/orbital-followup-report.md`. No landing or root spec/todo edits.
- Exo worker: separate repo/worktree/spec only.

## Edge cases
Unknown files, unknown trailing-slash routes, API errors, unavailable upstreams,
missing404 asset, empty conflict corpora, stale cache refresh failures, page
boundaries, operators without fleets, tied active counts, and concurrent cache reads.

## Acceptance criteria
- [x] Real local Caddy unknown paths return404 with the styled recovery page body;
  ordinary assets/pages and proxied failure behavior retain their expected status.
  Command: documented local Caddy smoke harness in `tasks/landing-followup-report.md`.
- [x] `/api/operators?sort=active` succeeds with descending active counts and a
  deterministic tie-break; unsupported sorts still422. Meaningful focused API test passes.
- [x] Status/stale conflict lists and counts share the same warm cached rows before
  slicing; no per-page duplicate expensive CTE. Cache/page/count tests pass including empty rows.
- [x] Covering Python tests run with explicit local-only/invalid DSNs and pass;
  TypeScript/Vite build passes if frontend changed.
- [ ] Desktop/375px local browser checks cover changed interactions and HTTP recovery.
- [ ] Independent read-only Codex verify covers base `ae3454e` through final HEAD;
  findings are reproduced and resolved, or recorded with a precise reason.

## Open questions
- Local reproduction proves the repository routing serves styled404. The single-file
  bind mount can retain replaced files; a stale mounted/active production configuration
  is a candidate, not a confirmed runtime diagnosis. Operator hash/state checks remain.
- Operator: deployed Caddy config/reload state may differ from repository config;
  source fixes cannot prove a production reload occurred.

## Decision log & lessons learned
- 2026-09-17 (Codex) — Public unknown path404 returned zero bytes while404.html
  returned200 and5764 bytes. Operators Active returned422. Treat both as open
  despite broader deployment health checks passing.
- 2026-09-17 (Codex, verified locally) — Caddy2.11.4 returned404 with5764-byte
  recovery HTML for unknown paths, preserved upstream404 and connection-failure502,
  and did not turn a missing recovery asset into200. Preserve the correct routing.
  Move configuration into a directory mount following official Caddy image guidance;
  adopting it requires operator-only Caddy recreation. A read-only body+status smoke
  check and checked hash/validate/reload runbook prevent status-only false positives.
- 2026-09-17 (Codex, browser verified) — Closing the mobile menu must reset
  aria-expanded as well as its CSS class.375px anchor navigation now does both.
- 2026-09-17 (Codex worker, reviewed) — Cache complete status/stale row sets and
  derive pagination totals/count helpers from them. Serialize cold computations but
  leave warm readers nonblocking. Existing independent stats-payload caching may lag
  a row refresh by its refresh cadence; this does not promise atomic cross-endpoint snapshots.
