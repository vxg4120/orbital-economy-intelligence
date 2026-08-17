# FCC Dockets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The governing spec is `docs/specs/fcc-filings.md`; its Constraints section is a hard boundary.

**Goal:** Surface how FCC filings relate over time as dockets keyed on callsign — a dated timeline per authorization, granted and pending together — without asserting supersession the data cannot support.

**Architecture:** Two latest-run-scoped views over already-ingested `raw_ibfs_filings` (no new tables, no extraction, no nightly step), docket summary fields riding the pending list, one new timeline endpoint, and a docket chip plus expanded-detail timeline in the Filings view. Specs are never merged across filings; each timeline row reports only whether its own validated extraction exists.

**Tech Stack:** PostgreSQL views, FastAPI, React/TS, pytest.

## Global Constraints

- Migration number **0021** (claimed with the peer session; 0022+ is theirs if needed).
- The `is_pending` predicate is copied verbatim from `v_fcc_pending_applications` and pinned by a both-directions EXCEPT test, so the two can never drift.
- Views over `raw_ibfs_*` must scope to the latest ok ingest run (accumulating-runs trap, measured 815k rows / 141k filings).
- Tests pin rules, never outcomes (three prior instances of outcome-pins breaking on the system working).
- Deploy outside the 07:10/19:10 UTC nightly windows; verify in-container.

---

### Task 1: Migration 0021 — the docket views, plus rule-pin tests

**Files:** Create `db/migrations/0021_fcc_dockets.sql`, `tests/test_filing_lineage.py`.

**Interfaces produced:** `v_fcc_docket_filing (callsign, file_number, app_type_code, date_filed, status_code, date_grant, date_deny, date_dismiss, date_surrender, is_pending)`; `v_fcc_docket (callsign, filings_total, filings_pending, filings_granted, pending_amendments, first_filed, last_filed)`.

- [ ] Write the migration: `v_fcc_docket_filing` = latest-run-scoped SAT filings with non-empty callsign, `is_pending` restating the canonical predicate (`date_filed IS NOT NULL AND date_grant/deny/dismiss/surrender all NULL`); `v_fcc_docket` = the group-by with counts and date range. Header comment carries the concurrent-MODs evidence and the no-parent-key reason.
- [ ] Before pinning uniqueness, measure it: `SELECT file_number FROM v_fcc_docket_filing GROUP BY 1 HAVING count(*) > 1` — expect zero; if not, that is an upstream finding to record, not to hide.
- [ ] Tests (all `@pytest.mark.db`, no literal counts): docket pending sum == canonical pending count with callsigns; internal consistency (`pending <= total`, `granted <= total`, `amendments <= pending`, `first_filed <= last_filed`); both-directions EXCEPT drift test; file_number uniqueness.
- [ ] `scripts/migrate.py`, run tests, commit: `Filings: docket views (0021), the timeline model the data actually supports`.

### Task 2: API — docket summaries on the list, and the timeline endpoint

**Files:** Modify `api/routers/filings.py`; extend `tests/test_filing_lineage.py`.

- [ ] `pending()`: `LEFT JOIN v_fcc_docket d ON d.callsign = p.callsign`, adding `docket_filings_pending`, `docket_filings_total`, `docket_pending_amendments`. Note text extended.
- [ ] `GET /filings/docket/{callsign}`: summary row + timeline (`ORDER BY date_filed NULLS LAST, file_number`), each row `LEFT JOIN fcc_spec_filing … AND is_validated` as `spec_available`; unknown callsign → `{summary: null, timeline: []}`, HTTP 200 (absence is a fact, not an error). Docstring and response `note` state the timeline-not-chain semantics.
- [ ] Tests: list rows with dockets satisfy `pending >= 1`, `total >= pending`; the timeline test self-selects a mixed docket (`filings_granted > 0 AND filings_pending > 0 ORDER BY filings_total DESC LIMIT 1`) and asserts: summary total == timeline length, both pending and granted rows present, dates ascending, file numbers unique, at least one `spec_available`; unknown-callsign shape.
- [ ] Run tests + marker hygiene; commit: `Filings: docket summaries on the pending list, and the docket timeline endpoint`.

### Task 3: Web — docket chip and expanded timeline

**Files:** Modify `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/views/Filings.tsx`, `web/src/api/fixtures/filings.json`; create `web/src/api/fixtures/filing_docket.json`.

- [ ] `types.ts`: `docket_filings_pending/_total/_pending_amendments: number | null` on `PendingFiling`; `DocketFiling` + `DocketResponse` interfaces.
- [ ] `client.ts`: `getFilingDocket(callsign)` following exactly the `getFilingDocuments` pattern including its mock-fixture branch.
- [ ] `Filings.tsx`: chip on rows where `docket_filings_total > 1` (`docket {pending}/{total}`, tooltip explaining the docket and amendments count); in `FilingDetail`, when `callsign` present fetch the docket and render the compact timeline (date · type · granted/pending · spec badge), current filing highlighted, mono per the Ledger rule.
- [ ] Fixtures: docket fields on all rows (null default, 2 populated incl. one large docket), `filing_docket.json` with a mixed timeline. `tsc --noEmit` + `vite build` clean; mock-mode render check.
- [ ] Commit: `Filings: docket chip and timeline in the web view`.

### Task 4: Gate, deploy, verify, close the spec checkboxes

- [ ] Chunked gate: `test_[d-f]` (filing_lineage lands there) + `test_[j-z]` fully; `[a-c]`/`[g-i]` if any touched file warrants.
- [ ] Check `tail refresh.log` and the clock against 07:10/19:10 UTC; push; box `git pull --ff-only && docker compose up -d --build oei-api`; `docker compose exec -T oei-api python scripts/migrate.py`; in-container verify.
- [ ] Live: `curl /api/filings/docket/S3069` shows granted + pending entries; pending list carries docket fields; three sites 200; geolocation header intact.
- [ ] Tick Increment A boxes in `docs/specs/fcc-filings.md`, commit spec update.
