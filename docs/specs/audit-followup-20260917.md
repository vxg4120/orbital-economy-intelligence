# Spec: Close independently reproduced site audit gaps

**Status:** complete (source verification; operator adoption tracked separately)
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
  deterministic tie-break; unsupported sorts still422. Use a fixture whose Active,
  Fleet, name and ID order differ; replacing Active with Fleet must fail the DB test.
- [x] Status/stale conflict lists and counts share the same warm cached rows before
  slicing; no per-page duplicate expensive CTE. Cache/page/count tests pass including empty rows.
- [x] Covering Python tests run with explicit local-only/invalid DSNs and pass;
  TypeScript/Vite build passes if frontend changed.
- [x] Desktop/375px local browser checks cover changed interactions and HTTP recovery.
- [x] Independent read-only Codex verify covers base `ae3454e` through final source HEAD;
  findings are reproduced and resolved, or recorded with a precise reason.

## Open questions
- Operator handoff now reports confirmed stale mount: host inode295597 contained
  handle_errors, container inode258983 did not. Claude independently recreated Caddy;
  Codex then verified public body+status smoke. Directory-mount adoption remains pending.
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

- 2026-09-17 (operator evidence + Codex public GET) — Claude confirmed stale mounted
  configuration and repaired the live404 by Caddy-only recreation. Five public smoke
  checks now pass, including random unknown paths with/trailing slash:404/5764 bytes.
  This live repair precedes adoption of the reviewed directory-mount source change.

- 2026-09-17 (independent Codex review, confirmed) — Initial Active-sort fixtures
  produced the same order under Fleet sorting. Strengthen behavioral regression data
  and prove a Fleet-sort mutation fails. No runtime/security finding was reported.

- 2026-09-17 (Codex, reproduced) — Commit9e0edfd makes all four sort orders differ.
  Correct source passed14 focused tests; Fleet/Name/ID mutations each failed the
  primary order assertion. Independent review found no runtime or security defect.
