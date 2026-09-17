Reviewed `ae3454e…dd04f6b`. **One P2 test-validity finding; no additional actionable runtime or security defects found.** Concurrent uncommitted documentation changes were excluded.

1. **P2 — Active-sort fixtures cannot distinguish Active from Fleet sorting.** [tests/test_api_orbital_followup.py:212](/Users/vgupta/Development/repos/wt-codex-space-followup-20260917/tests/test_api_orbital_followup.py:212) gives operators 10, 20, and 30 identical total fleets. Both sorting expressions therefore produce `[10, 20, 30, 40]`. I verified that mapping `active` to the Fleet expression still passes the non-DB test; calculating the SQL fixture’s expected orders confirmed the same blind spot. The retained browser fixture repeats it. Add a larger fleet with fewer active satellites and an earlier operator ID, while retaining tied-active and zero-fleet cases. The implemented SQL expression itself is correct.

| Acceptance criterion | Result | Evidence and limits |
|---|---|---|
| Real Caddy styled 404; ordinary/proxy statuses preserved | **PASS — recorded validation** | Reviewed the three-test harness and retained recovery evidence. Current Caddy adaptation passed; HTTP rerun blocked by sandbox temporary-file restrictions. |
| Active sort, deterministic ties, unsupported sorts 422; meaningful test | **FAIL — test adequacy** | Allowlisted implementation is correct; regression coverage has the P2 gap above. |
| Shared conflict rows/counts; no duplicate page CTE; empty results | **PASS** | Focused tests exercise shared snapshots, pagination, empty lists, cold concurrency, failed refreshes, and nonblocking warm reads. |
| Python checks with explicit safe DSNs; conditional frontend build | **PASS — scoped** | Fresh run: **13 passed, 2 DB tests deselected**, using the required invalid DSN. Ruff and diff checks passed. TypeScript/Vite source is unchanged. |
| Desktop/375px interaction and recovery checks | **PASS — retained evidence** | Inspected browser harness/results and screenshots. Actual changed routes were exercised; `/api/stats` was a fixture, as disclosed. |
| Independent review through final HEAD | **PASS — finding recorded** | Review covers `dd04f6b`; P2 remains unresolved because this review permits no edits. |

The Caddyfile is byte-identical to the base. Compose rendering confirms the read-only directory mount and preserved named data/config volumes. This matches [official Caddy image guidance](https://github.com/docker-library/docs/blob/master/caddy/README.md#-do-not-mount-the-caddyfile-directly-at-etccaddycaddyfile). Actual container mount/recreation behavior remains untested here.

Proposed verified spec amendments:

- Require Active-sort fixtures where Active, Fleet, and ID-only ordering differ; substituting Fleet sorting must fail.
- Clarify that count/page consistency applies to one row-cache snapshot. The browser fixture does not validate the independently cached real `/api/stats` endpoint.
- Record routing validation, Compose mount validation, and actual container migration as separate evidence.

**Production state was not inspected.** No edits, agents/messages, DB connections, deployment, or memory writes were performed.

## Parent resolution — 2026-09-17

The P2 was confirmed and fixed in `9e0edfd`. The real SQL fixture now gives Active
order [30, 10, 20, 40], Fleet [20, 10, 30, 40], Name [20, 30, 40, 10], and IDs
[10, 20, 30, 40]. Tied active counts cross a page boundary; zero-fleet remains.
All 14 focused tests passed in a disposable local PostgreSQL. Three in-process
mutations mapping Active to Fleet, Name and ID each failed the exact order assertion.
No runtime source changed after the reviewed `dd04f6b`.

Parent inspected the fixture, changed report, cache/query diff, browser evidence,
and Caddy HTTP checks. This resolves the finding without changing public behavior.
Targeted read-only follow-up verification is recorded after completion.
