# Spec: FCC Filings — the pre-launch pipeline

**Status:** active
**Owner:** Vib
**Repos touched:** space (OEI)
**Last updated:** 2026-08-24 (Increment B shipped at c44f22b; C's landing mention live; announcement draft awaiting Vib's venue call)

## Goal

Make the FCC space-station docket a first-class, queryable layer of OEI: what has been filed,
what it proposes (structured, cited specs), and how filings relate to each other over time. This
is the forward-looking half of the platform — filings describe satellites months to years before
they exist in any tracking catalog (Starlink Gen1 filed 14 months ahead; Kuiper 39) — and it is
built to the same standard as the rest of the site: every value carries provenance a reader can
check, and every coverage limit is published rather than smoothed over.

## Architecture decisions

- 2026-08-17 — Model filing relationships as **dockets keyed on callsign**, not supersession
  chains. Rejected: parent-pointer chains with field-level inheritance. Because: measured on the
  live corpus, SpaceX's S3069 docket carries **four concurrent pending MODs** each modifying a
  different aspect of the same authorization (Gen2 shells, V3, 2 GHz MSS, 1.5-1.6 GHz MSS) —
  the data is a docket, not a chain — and `raw_ibfs_filings` carries no lead-filing key, so parent
  pointers would be invented structure. Field inheritance across amendments needs the amendment
  *text* read, which is the (gated) LLM layer's job, later.
- 2026-08-17 — Dockets are **pure views over already-ingested data** (migration 0021), no new
  tables, no new extraction, no nightly step. Rejected: a materialized lineage table. Because: the
  group-by is over ~141k rows and is trivial at query time, and a matview would import the
  snapshot-vs-live test hazards this repo has already paid for twice.
- 2026-08-17 — Every view over `raw_ibfs_*` **must scope to the latest ok ingest run** (the
  `latest_filings` CTE pattern from `v_fcc_pending_applications`). The raw tables accumulate every
  run's copy (~815k rows for ~141k filings); an unscoped aggregate silently multiplies counts.
- 2026-08-11 — Schedule S values are extracted **deterministically, never by a model**. The FCC's
  filing tool generates Tech Reports in fixed label-value layout; a regex is exact where a model
  would be plausible-and-wrong, which is the one failure a receipts platform cannot absorb.
- 2026-08-11 — Document detection is **by content anchors, not filenames** (`OMB 3060-0678`,
  `312 File Number:`, `Select Orbit Type`). The generated report contains no "Schedule S" or
  "Form 312" string anywhere; 558 distinct doc_name values make names untrustworthy.
- 2026-08-11 — **Citations are per field, with an independent validator.** A plane's values
  straddle page breaks (17 of 74 planes on SATAMD2017030100030); extraction and validation are
  separate code paths, and no row is served until its cited page is confirmed to carry its value.
- 2026-08-11 — Gateway truncation is a **coverage fact, not an error** (`fetch_status='truncated'`),
  and as-filed sentinel values (lunar 1/1/0 and 99999) are **served as filed and flagged**, never
  corrected — a silent repair would disagree with the very page we cite.

## Constraints

Hard boundary for all work, and especially for anything autonomous or overnight:

- **No LLM extraction calls of any kind** until (a) the 40-filing stratified eval set exists and
  (b) Vib has explicitly approved the spend. Metered APIs are money; this is not discretionary.
- **Machine-derived data never enters headline metrics** — no leaderboard, no monthly snapshot,
  no landing-page stat derives from extracted specs (BUS_BENCHMARKS_METHODOLOGY §5.8 regime).
- No served spec value without a validated per-field page citation. Never widen validation to
  "nearby pages." Never correct an as-filed value; flag it.
- Views over `raw_ibfs_*` must latest-run scope (see decision above).
- Migration numbers are claimed by session message before use; a pushed migration is never
  amended, only superseded. 0021 is this workstream's docket views.
- Deploy is `git pull` + `docker compose up -d --build` + **in-container verification**
  (`scripts/`, `ingest/`, `api/` are image-baked); never during the 07:10/19:10 UTC nightly
  windows (the 2026-07-30 19:10 run was silently lost to a mid-rebuild collision); Caddyfile
  changes additionally need `caddy reload`.
- One pytest suite or harvest batch against the dev DB at a time, claimed by message. The full
  suite runs chunked (`test_[a-c]` / `[d-f]` / `[g-i]` / `[j-z]`); the g-i chunk alone is ~10.5 min.
- Overnight autonomous runs: feature branches, MRs only, no deploys, no prod writes — per the
  global standing policy.

## Interfaces & dependencies

- **Consumes:** `raw_ibfs_filings` (nightly, 72h-gated bulk dump), `v_fcc_pending_applications`
  (the canonical pending predicate), `fcc_spec_filing/orbital/band` (0020, validated rows only),
  `fcc_filing_document` + ICFS harvest (weekly gate), `fcc_filing_note` (curated YAML).
- **Exposes:** `/api/filings/pending` (list + spec_* + docket_* summaries),
  `/api/filings/{n}/spec` (cited per-plane detail), `/api/filings/{n}/documents`,
  `/api/filings/docket/{callsign}` (dated timeline, granted + pending). Web: `07 FILINGS` view.
- **Nightly:** `fetch_filing_documents.py --if-stale` (≈weekly) then
  `extract_filing_specs.py --if-stale` (≈weekly), both inside the 19:10 oei block. Dockets add
  no nightly step.

## Edge cases

- Concurrent MODs on one docket are the **normal case** (S3069), not an anomaly. The UI must never
  imply one pending MOD supersedes another; specs are never merged across filings.
- Callsign coverage varies by type: LOA 131/131, MOD 117/118, AMD 59/62, but STA 114/223 and
  T/C 7/22. Filings without callsigns are simply docketless rows — shown plain, never forced into
  a group.
- Old dockets span decades and applicants (SES S2434: 13 pending filings back to 2004). The docket
  shows per-filing applicants; it never asserts a single owner.
- SATLOA2016062200058's 28 attachments are truncated server-side by the FCC gateway (455 bytes vs
  a declared 87,684) — published as a coverage gap, status `truncated`.
- Lunar filings carry sentinel orbital values because Schedule S cannot express a translunar
  trajectory (measured: Intuitive Machines and Lockheed file 1/1/0; Astrobotic files 99999).
  Excluded from summary ranges, counted visibly, flagged `as_filed_implausible`.
- Schedule S exists for 65 of 136 harvested filings; narratives carry **no numeric orbital
  parameters** (measured), so absent coverage is a fact about the corpus, not a parsing failure.

## Acceptance criteria

Increment A — dockets (in progress):
- [x] Migration 0021 creates `v_fcc_docket_filing` and `v_fcc_docket`, latest-run scoped.
      Verify: `sum(filings_pending)` over `v_fcc_docket` equals
      `count(*) FROM v_fcc_pending_applications WHERE callsign <> ''` (531 at spec time; the test
      pins the *equality*, not the number).
- [x] The docket views' pending predicate cannot drift from the canonical view. Verify: a test
      diffs `v_fcc_docket_filing WHERE is_pending` against `v_fcc_pending_applications` by
      file_number, both directions, empty.
- [x] `/api/filings/pending` rows carry `docket_filings_pending/_total/_pending_amendments`.
      Verify: pytest `tests/test_filing_lineage.py`; S3069 rows show total ≥ pending, granted ≥ 1.
- [x] `/api/filings/docket/{callsign}` serves the dated timeline (granted + pending) with
      per-filing `spec_available`. Verify: curl S3069 on prod returns both granted and pending
      entries, dates ascending.
- [x] Filings view shows a docket chip on multi-filing rows and the docket timeline in the
      expanded detail. Verify: tsc + vite build clean; mock fixtures carry docket fields.
- [x] Deployed and live-verified outside the nightly windows.

Increment B — documentation (next, per Vib 2026-08-17 "work extensively on marketing and
documentation"):
- [x] A published extraction-methodology section: deterministic method, per-field citations,
      validator, coverage split (Schedule S 65/136), truncation disclosure, sentinel policy.
      Verify: reachable from the Filings view; curl + grep for the coverage numbers.
- [x] The DQ report gains a filings/spec section (counts, validation rate, truncated inventory).
- [x] Codex verify pass over this spec's increments, findings triaged into the decision log.

Increment C — marketing (scope with Vib before starting):
- [ ] Changelog/announcement draft for the filings + specs + dockets feature set, in Vib's voice,
      no em dashes, claims limited to measured numbers with their n attached.
- [x] Landing page (vibcreates.com) OEI entry mentions the pre-launch pipeline with one honest
      metric. Verify: live curl.

Deferred, unchanged: shell rollups (must cite plane rows + record the grouping rule); the
40-filing eval set (20 dev / 20 locked, stratified); the LLM prose layer (gated on eval set +
cost approval); AMD-to-parent attribution (needs amendment text; belongs to the LLM layer).

## Open questions

- **Slash-bearing file numbers break the path routes.** T/C and A/O types (23 pending) contain
  a literal slash, so /filings/{file_number}/spec and /documents cannot address them; Starlette
  splits on the decoded path. Needs a route redesign (query-param form or dual-segment routes).
  Found by Codex verify 2026-08-24. Assign: Claude.
- **Blob bytes are not retained.** The store holds sha256 + page counts, so a reissued attachment
  is detectable on refetch but not replayable; re-verification depends on continued FCC
  availability. Disclosed in the methodology 2026-08-24; actual byte storage is open. Assign: Vib
  (disk/scope call).
- **Multiple Schedule S documents on one filing** (95 docs across 64 filings): versions or
  multipart? Today the first extracted document wins the summary and the response is keyed to it.
  Define ordering semantics before extracting the rest. Assign: Claude.
- **Full-inventory content sweep**: candidates are name-selected then content-confirmed, so a
  Schedule S attached under an unrelated name would be missed and absence claims are name-scoped
  (now disclosed). Sweeping all ~660 PDFs would close that at real politeness cost. Assign: Vib.
- Granted-filing spec extraction (the `current_authorized` baseline): extend 0020's extractor
  beyond the pending set? Real value for docket views, real harvest cost (~2k more documents).
  Assign: Vib (scope), then Claude.
- Marketing scope and venues for Increment C (site only, or also LinkedIn/announcement post?).
  Assign: Vib.
- Log rotation for `deploy/refresh.log` (297,746 lines, unrotated, and it is the only record of
  nightly failures). Assign: Claude, small, fold into Increment B.

## Decision log & lessons learned

- 2026-08-11 (Claude, verified) — Schedule S Tech Reports never name themselves; anchor on
  `OMB 3060-0678` / `312 File Number:` / `Select Orbit Type` (3/3 measured), never on the form's
  name (0/3).
- 2026-08-11 (Claude, verified) — pypdf renders these tables one cell per line; fixtures written
  from `pdftotext -layout` hid two real bugs (label truncated to "Service"; header swallowed).
  Build fixtures from pypdf output.
- 2026-08-11 (Claude, verified) — plane fields straddle page breaks (17/74 on the Boeing V-band
  filing); row-level citations are wrong in the worst direction; cite per field.
- 2026-08-11 (Claude) — the validator confirms presence, not completeness: a truncated capture
  ("Service") still validated because the word is on the page. Correctness needs both the parse
  and the check.
- 2026-08-11 (Claude, verified) — api-prod truncates some attachments server-side (455B with a
  declared length of 87,684; identical under four clients); record as `truncated`, distinct from
  `parse_error`, or it reads forever as a 54% parser failure.
- 2026-08-12 (peer session, verified) — outcome-pins break on the system working (COL alias,
  Planet fleet counts, matview currency ×2); pin rules, not outcomes. Third-instance pattern.
- 2026-08-17 (Claude, verified) — the failure cluster in refresh.log dates to 2026-07-30 19:10:
  one nightly fired mid-rebuild ("service oei-api is not running" ×6, including a spurious slug-
  gate alarm) and sat unnoticed 18 days because every step soft-fails into the log. Deploy outside
  nightly windows; read the log after rebuilds.
- 2026-08-24 (Codex-verify, applied) — the validation contract must enumerate EVERY served
  field. lifetime_years, band service and direction were served while sitting outside the
  fieldwise contract, which made them checked-looking but unchecked; caught by the cross-provider
  pass, fixed in extractor 1.1, and prod re-extracted. String validation whitespace-normalizes,
  because wrapped labels differ from their page only by a line break.
- 2026-08-24 (Codex-verify, applied) — one response, one receipt identity: /spec now keys planes,
  bands and the source document to the summary's (file_number, sys_id), because a filing can
  carry more than one Schedule S attachment and unioning children across documents hands the
  reader citations into a document they are not looking at.
- 2026-08-24 (Codex-verify, applied) — the lunar sentinel is the whole row: inclination 0 is part
  of the same placeholder as apogee 1, so summary ranges exclude the row, not just the altitude.
- 2026-08-24 (Codex-verify, applied) — a scoped extraction run (--file-number/--limit) is
  ledgered under schedule_s_specs_partial and confers no weekly freshness, because a one-filing
  run satisfying the full-sweep gate would silently skip the sweep for a week.
- 2026-08-24 (Codex-verify, applied) — mock fixtures must preserve domain invariants: null
  callsign means null docket fields, and every row sharing a callsign carries the same docket
  summary. A fixture that violates the model it mocks tests the wrong product.
- 2026-08-24 (Vib, applied) — per-attribute conflict badges must compare CANONICAL values,
  never raw per-source vocabularies. K2 Space's Gravitas badged four false conflicts (Gravitas vs
  GRAVITAS, P vs PAY, O vs +, K2SP vs US); the headline conflict machinery had always compared
  canonically with UNKNOWN excluded, and the detail endpoint had quietly diverged into a
  stricter, noisier rule. GCAT status O (in orbit) is a no-claim about operations and cannot
  disagree; SATCAT's owner is a jurisdiction code and is not commensurable with an organization
  identity. Fixed in api/routers/satellites.py by reusing norm_name, canonical_object_type,
  parse_date_loose and status_mapping; the repo-wide sweep found no other raw comparison.
- 2026-08-17 (Claude, measured) — lineage evidence: callsign is near-universal on LOA/MOD/AMD,
  absent on half of STAs; 84 multi-filing pending dockets hold 251 of 667 pending; S3069 = 28
  filings (19 granted, 9 pending, 4 concurrent MODs with specs); `raw_ibfs_filings` has no parent
  key; raw tables accumulate runs (815k rows / 141k filings) so views must latest-run scope.
