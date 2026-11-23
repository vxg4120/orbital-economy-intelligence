# Phase 0 + Phase 1 — todo

## 2026-09-17 — User-confirmed first QA year

- [x] Verify deployed graphic and release branch incorrectly say2015.
- [x] Restore first-QA constellation milestone to2014, confirmed directly by Vib.
- [x] Preserve separately listed employer dates; inspect the exact one-line HTML diff.
- [x] Operator published corrected landing page; public GET independently verified first-QA milestone2014 on2026-09-17.

Review: minimal factual correction on isolated branch from release/audit-20260917;
no runtime behavior changed, no production action performed.

Plan: docs/plans/phase-0-1-plan.md · Spec: docs/SPEC.md

- [x] Task 1: Repo foundation (scaffold, docker-compose, tooling)
- [x] Task 2: Database schema — migrations + runner
- [x] Task 3: Ingestion layer (network + landing, politeness enforced)
- [x] Task 4: Identity graph engine (normalize/match/merge/resolve + seeds)
- [x] Task 5: Metrics SQL + DQ & Conflict Report
- [x] Task 6: Live pulls, E2E Phase 1 build, CI, README
- [x] Final multi-lens review + fixes (5 lenses, adversarial verification, 11/11 findings fixed)
- [x] BONUS: GCAT orgs.tsv operator enrichment (owner coverage 2.4% → 100% of on-orbit payloads)
- [x] GP element sets landed after 2h politeness window (15,932 rows, cagg + operator views live)
- [x] USER ACTION: request Space-Track account (creds → .env)
      Done in July: Vib had the account active on 2026-07-09, and the credentials have been on the
      box since 2026-07-22 (checked 2026-11-13).

## Review

### Task 6 — live data, E2E build, CI, README (integration)

**Reconciliation (loaders vs. real headers), done before the live pull:**
- GCAT `parse_tsv` was landing the `# Updated <date>` banner line (second line of every GCAT file)
  as a bogus one-column row. Fixed: body lines starting with `#` are skipped. Fixture + test added.
- GCAT numeric coercion (`perigee/apogee/inc`, `norad`) and SATCAT date/numeric coercion made
  defensive: one malformed cell in a 40k–70k-row pull now degrades to NULL (raw kept in `extra` for
  GCAT), instead of aborting the whole landing transaction. Tests added.

**Live pulls (each endpoint once; verified `skipped_fresh` on rerun, zero HTTP):**
- SATCAT bulk: 69,705 rows / 6.6 MB · GCAT satcat: 69,935 / 19 MB · GCAT psatcat: 27,879 / 5.0 MB ·
  GP active: 15,932 / 6.7 MB · SupGP: 0 rows (best-effort, ok) · UCS: graceful skip (no local file).

**Identity build on real data:** 69,878 canonical satellites; 99.75% of GCAT-with-NORAD linked to
SATCAT; 418,086 audited `merge_log` events; 35 status disagreements; status coverage 76.5%.

**Performance fix (integration):** the deterministic matcher's per-row round-trips ran >13 min on
the full catalog. Rewrote the two big passes (SATCAT, GCAT-NORAD) set-based (COPY into a temp table
+ a single `INSERT … RETURNING → merge_log` CTE that preserves the "audit only newly-created links"
idempotency), and pipelined the resolver's per-satellite write loop. Full build now ~45s. Also
deduped by NORAD (`DISTINCT ON`) — live GCAT registers several JCAT pieces under one Satcat number,
which broke a naive `ON CONFLICT DO UPDATE`.

**DQ report fixes (flagship artifact, `docs/reports/dq_report.md`):**
- Ledger table keyed on `(source, endpoint, status)` — all three CelesTrak endpoints share
  `source='celestrak'`, so the old `(source, status)` grouping hid the satcat and gp pulls.
- Status-disagreement section now compares each source's *asserted* status (via `status_mapping`),
  not the resolver's single winning status (which made disagreement structurally invisible → 0).
- Decay-date conflicts compared on *parsed dates* (4,229 real) rather than raw strings (35,277, most
  of them just `1957 Dec  1 1000?` vs `1957-12-01` formatting).

**Deliverables:** `Makefile` `metrics`/`report` targets; `.github/workflows/ci.yml` (ruff + unit,
then a pg17 service-container db job); README rewrite; this file.

**Verification:** `pytest -q -W error` → 111 passed (109 + 2 new). `ruff check .` clean.

### Final review + enrichment wave

- 5-lens review (correctness, spec, data-model, silent-failures, test-quality) with per-finding
  adversarial refutation: 5 confirmed serious + 6 minors, all 11 fixed with covering tests.
  Standouts: half-open SCD2 range join (the spec's own illustrative BETWEEN double-counted the
  transition day — proven live on INTELSAT I); SupGP parser rewritten against the real
  warn-badge page structure (was silently landing 0 rows with status ok).
- Operator enrichment: GCAT orgs.tsv as data-driven operator source (1,435 operators, 4,391
  aliases, 128 subsidiary edges; seed's 17 canonical operators + M&A chains take precedence).
  On-orbit owner coverage 2.4% → 100%; unmatched-owner backlog 1,969 → 8 (JV combined codes).
- GP landed after the 2h window (15,932 OMM element sets); sat_daily cagg refreshed; every daily
  bucket operator-attributed via the identity graph.
- Fixed a real-time-cagg test pitfall: synthetic-data tests must use dates above the
  materialization watermark (dynamic future dates), else rows vanish under rollback isolation.
- Final: 151 tests, -W error, ruff clean. DQ report: 100% operator coverage, 76.8% status
  coverage, 100% of on-orbit payloads with >=2 source identifiers, 35 SATCAT-vs-GCAT status
  disagreements, 4,229 decay-date conflicts.

### Frontend wave (Orbital Economy Terminal)

- FastAPI read-only API (9 endpoints, Postgres-level read_only enforced) + React/Vite/TS SPA,
  built by two parallel agents against a locked DTO contract; F3 integration fixed 2 type drifts,
  14/14 e2e endpoints green on the live DB. SPA deep-link catch-all added.
- Views: Overview (stat tiles, coverage meters, conflicts panel, ingest ledger, congestion
  teaser), Resolver (crosswalk + SCD2 ownership timeline + assertions), Conflicts (3 tabs),
  Operators (league table + detail). Screenshots in docs/img/, wired into README.
- `make fe` → http://localhost:8600. Suite: 169 passed -W error; pnpm build clean.

# Bus Benchmarks (2026-07)

Provenance-tracked performance scoreboard for spacecraft buses by manufacturer and bus model.

- [x] Migration 0009: satellite_bus attribution table + bus_benchmark_snapshots (monthly, immutable)
- [x] Build step: identity/bus.py + scripts/build_bus.py (normalize bus strings, resolve orgs, parent rollup, source_assertion rows, matview refresh, monthly snapshot; wired into daily_ingest.sh)
- [x] Metrics: metrics/bus_benchmarks.sql (mv_bus_behavior_sat + v_bus_sat + per-manufacturer / per-bus benchmark views reusing existing station-keeping / TTO / disposal definitions)
- [x] API: api/routers/buses.py (leaderboard, methodology v1.0, slug detail, per-metric provenance receipts, history)
- [x] Web: 05 BUSES view (leaderboard, detail, receipts, monthly record, methodology panel), mock fixtures
- [x] MCP: mcp_server/ stdio server with bus_benchmarks + bus_detail tools (dependency-free; official SDK does not install on py3.14 toolchain)
- [x] Docs: docs/BUS_BENCHMARKS_METHODOLOGY.md (v1.0, changelog, provenance guarantee, correction channel)
- [x] Tests: router + view cohort tests, build normalization tests, MCP protocol/tool tests (272 total pass with -W error)
- [x] Gates: pytest -W error PASS, ruff clean on changed files, pnpm -C web build PASS; live-db verified (27,849 payloads attributed; 277 manufacturers and 426 bus models at n >= 5)

## Review (Bus Benchmarks)

- Attribution: 27,849 of ~27,933 GCAT payloads got a manufacturer (99.7%), 27,512 a bus model;
  3 org codes unresolved. 12,691 rows rolled up via the curated SPXS -> SPX override, 3,036 via
  GCAT parent chains (business-class parents only, so design bureaus never collapse into
  ministries/agencies).
- Headline (fleet-sorted): SpaceX 12,835 (TTO p50 78 d, SK share 77.3%, SK p50 0.042 km),
  OneWeb 650 (SK share 100%, SK p50 0.005 km), Planet 551 (SK share 0%: passive doves),
  Amazon/Kuiper 398. Disposal-compliance n is ~0 everywhere (v_deorbit_compliance still sparse);
  published as n=0 with no rate rather than a fake 100%.
- Slug fix mid-build: '+' is load-bearing in bus names (BSS-702MP+ vs BSS-702MP), preserved as
  '-plus' in slugs; leading-apostrophe GCAT markers stripped from display names.

## 2026-09-17 — Close remaining site audit defects

Spec: `docs/specs/audit-followup-20260917.md`.

- [x] Recheck peer/source state and isolate from released ae3454e.
- [x] Prove local404 routing; correct config mount and add checked operator smoke.
- [x] Implement valid Operators Active sort and shared conflict warm caches.
- [x] Run hermetic focused tests and desktop/375px browser verification.
- [x] Commit reviewed source, run independent Codex verification and resolve findings.
- [x] Record deployment/configuration prerequisites separately from source completion.

Review: source fixes verified; independent review found one test-validity issue,
reproduced and resolved in9e0edfd.14 focused SQL/API tests and3 Caddy tests passed;
desktop/mobile browser evidence passed. See tasks/independent-followup-review.md.
Live404 was repaired independently by Claude and confirmed by public GET; directory
mount adoption and API release remain operator-owned. No production actions by Codex.

Public follow-up: the new mobile menu markup and Active sort are now observable
live. Four conflict page requests returned200 in about0.65s with consistent totals.
The exact active Caddy mount and deployment revision remain operator evidence.

Operator completion recorded in CannMenus handoff: space `bf8c6ed` deployed with
the permanent Caddy directory mount. Codex's independent public behavior checks
passed. Source verification and operator release for this Space batch are complete.

## 2026-10-12 — Retire the per-run assertion ledger (assertion-model.md, criterion 7)

Condition met: nine green nightlies on the claim table (2026-10-07 19:10 to 2026-10-11 19:10).

- [x] Migration 0024: rename `source_assertion` to `source_assertion_runs_20261012`, drop its
      FK to satellite, drop `claim_is_current`.
- [x] One writer: `identity/claims.py` owns the attribute lists and `_latest_run`; the
      bootstrap gate goes (no ledger to replay); `identity/assertions.py`, `build_claims.py`,
      `compare_claims.py` deleted; `build_graph.py` and `build_bus.py` stop calling `extract`;
      `merge.py` stops repointing the ledger.
- [x] Tests moved to the claim table (claims, sibling links, merge, reconcile, pipeline,
      migrations, bus build, conflicts fixture); full suite green on the local DB after 0024.
- [x] Codex verify against the spec; findings resolved.
- [x] Deploy outside the windows: pull, rebuild oei-api, migrate; verify views, resolver
      timing (criterion 6), site; spec criteria 5-7 and decision log; memory; peers told.
- [x] After 2026-10-19: `DROP TABLE source_assertion_runs_20261012`, df. Done 2026-11-11 20:44
      UTC by space-ed: 5 GB freed, box 39% -> 33%; claim endpoints 200 afterwards.

Review: deployed 2026-10-12 00:36 UTC (main a8f783d), migration applied on production in the
same minute; v_current_claim 704,324 rows, progress unchanged, sites and the conflicts, satellite
and buses APIs 200. Codex found two things, both taken: the renamed table is not a rollback by
itself (procedure written and rehearsed) and the missing gate could strand un-replayed history
(migration preflight, both branches exercised). Criterion 5 closes 2026-10-14; the DROP after
2026-10-19.


# Improvement batch, 2026-10-16 (Vib: "Do all of that", "Also work on improving design")

Owner: session space-7e. Peers: space-28 owns deploy/landing/* copy tonight (branch
landing/oct-2026-copy); space-a2 owns the source_assertion_runs_20261012 drop after 10-19.

## Needs nothing from Vib
- [x] 1. orbital `/api/health` route (parity with exo's), covered by a test, deployed 10-16 02:57 UTC (c6c7b2d).
- [x] 2. Latency: `/api/buses` 4.2 s -> 1.4 s (SQL 3.3 s -> 0.19 s, migration 0025
      mv_latest_satellite_status); exo landing cold 2.4/2.8 s -> 0.66/0.63 s (warm caches,
      exodossier a86c356). Both measured on the box after deploy.
- [x] 3. CI: tried seeding the fixtures before pytest; 5 of 96 graph tests pass, the rest
      are by design about the real catalog. Not viable; the skip-on-empty rule stays. The
      experiment found and fixed the audit_report crash on an empty Eutelsat cohort.
- [x] 4. Restore rehearsal passed 10-16 02:41-02:50 UTC (procedure in deploy/README.md).
- [x] 5. Phone/accessibility pass shipped on both apps 10-16 03:13 and 03:19 UTC (space 8436531,
      exodossier e8341d9): the stat strip, nav and tables get scroll cues, 10px text gone on
      phones, --ink-ghost text to --ink-faint, 36px+ touch targets, skeleton loading, exact
      figures on compact numbers, h2 panel titles. Open: whether exo adopts orbital's serif
      heading layer (P2-4), Vib's call.

## Needs a decision from Vib (present, don't build)
- [x] 6. Space-Track: creds are wired through nightly-refresh.sh and the client, but only
      scripts/backfill_gp_history.py uses the client. Confirm what the nightly gains, then
      Vib pastes creds into /root/apps/space/deploy/.env himself.
      Decided 2026-10-16 (Vib: "Yes to all") and built as the Space-Track top-up and decay landing,
      which has run green every night since 2026-10-16 (checked 2026-11-13).
- [x] 7. Exo light curves: nothing live reads the pipeline; the Docker image omits
      pipeline/. Decision is whether to build Wave 2 (recovery results on the site).
      Decided 2026-10-16 (Vib: "Yes to all") and built as Wave 2, which is live at /lightcurves with
      58 TOIs processed (checked 2026-11-13).
- [x] 8. GCAT: main fix shipped 10-07. Two open decisions: bus.py rule 2 for NORAD-less
      rows; whether status_map.yml keeps GCAT code C -> DECAYED (23 objects).
      Both decided 2026-10-16 (Vib: "Yes to all"): a NORAD-less GCAT row now attributes by its key's
      own number (methodology 1.10, 554 rows), and code C stays DECAYED (23 objects) (checked
      2026-11-13).
- [x] 9. 31% vs 31-37% copy: handed to space-28; source is splitstep build-journal.md:95-96.

## Review (2026-10-16 03:20 UTC)
Shipped and measured on the box: /api/health (orbital); /api/buses 4.2 s -> 1.4 s; exo landing
cold 2.4+2.8 s -> 0.66+0.63 s; restore rehearsal passed and documented; phone pass on both
apps (audit and after-metrics in the session scratchpad, report tables in the merge commits'
branches). Not done, by finding: CI graph tests cannot run on fixtures (5/96 pass), so the
skip-on-empty rule stays. Reviews: Codex was down for the account all night ("model is not
supported when using Codex with a ChatGPT account", needs `codex login`); each diff got an
independent Claude review instead, which caught two CI-breaking tests and an unbounded
cache staleness, both fixed before deploy. Lesson recorded: never Write over tasks/todo.md.

# Decisions batch, 2026-10-16 07:12 UTC (Vib: "Yes to all" to items 6-8 and P2-4)
Owner: session space-7e. One implementer per item in its own worktree/branch, an independent
review each, then merge and deploy. space-28 owns landing/stories-20261016 in both repos
(landing route + its own read-only endpoints) at the same time; we keep off those paths.
- [x] A. (merged f3cbe6f, deployed 08:15 UTC) Exo serif headings (P2-4): port orbital's Fraunces heading/display layer to exo.
      Branch exodossier design/serif-headings-20261016.
- [x] B. (merged a8a1357, deployed 07:53 UTC, migration 0027; awaits creds) Space-Track: periodic gp_history top-up + decay-message landing, as a nightly step
      that no-ops without SPACETRACK_IDENTITY/PASSWORD. Branch space feat/spacetrack-topup.
      Vib creates the account and pastes creds into /root/apps/space/deploy/.env himself.
- [x] C. (merged 882605c, deployed 08:15 UTC with migration 0007; found: the nightly graph build truncated the result tables via FK cascade, so results are now keyed on TOI; production run from the laptop in progress) Exo light curves Wave 2: a page showing transit-recovery results from the pipeline.
      Branch exodossier feat/lightcurves-wave2.
- [x] D. (merged 0d9a384, deployed 07:53 UTC, migration 0026; 0 moves, 557 relabels) GCAT: bus.py rule 2 for NORAD-less rows follows the key's own number (methodology
      1.9 -> 1.10, changelog); GCAT code C -> DECAYED stays, documented as decided.
      Branch space feat/gcat-rule2.
- [x] E. (space-28) Landing data stories on both apps, reviewed by space-7e, deployed with Vib's
      "Deploy the landing stories": orbital b9bccdf live 08:10 UTC, exo 2fed917 live 08:16 UTC.

## Review (2026-10-16 08:50 UTC, decisions batch)
All four "yes to all" items live, each independently reviewed before merge (Codex still down
for the account; Claude opus reviewers instead): exo serif headings (exodossier f3cbe6f, +118 KB
latin woff2); Space-Track nightly top-up + decay landing (space a8a1357, migration 0027, no-ops
with blank creds, verified on the box; Vib pastes creds into deploy/.env); /lightcurves page
(exodossier 882605c, migration 0007; the real finding was that the nightly graph build's
TRUNCATE candidate CASCADE had been wiping the result tables since July, so results are now
keyed on TOI; production populated from the laptop 08:17-08:41 UTC, 58/58 ok, 16/20, 20/20,
16/18 live); GCAT anchored_jcat_number rule (space 0d9a384, methodology 1.10; prod effect 0
moves, 557 relabels; code C -> DECAYED kept). Reviews caught: two data-loss paths in the
Space-Track cursor (empty window, no publication margin), a wrong-password loop, a missing
ON DELETE CASCADE on the light-curve FKs, and a churn flag over-counting on the new GCAT rule;
all fixed before merge. Watch: the 19:10 UTC rebuild is the first live test of 0007
(`/api/lightcurves/summary` .processed must stay 58). Side fixes: local Docker VM disk freed
(29 GB of stale ECR images); the scratch DB on 5439 restarted.

# Nav + SEO batch, 2026-10-19 09:00 UTC (Vib: "keep going", "improve the nav greatly" so
# projects are reachable directly, "also do some work on SEO")
- [x] A. (merged 551ac7d, live 09:56 UTC) Landing nav: "projects" disclosure listing CannMenus, Orbital, ExoDossier, SplitStep
      directly (desktop + phone), anchor ids on the write-ups. Branch space landing/nav-seo-20261019.
- [x] B. (same merge) Landing SEO: OG/Twitter/author/theme-color, JSON-LD WebSite + ItemList, canonical on
      /fun/ and /splitstep/, dated sitemap. Same branch. (Description line and og.png belong to
      space-28's copy branch, merged 2a8e3d4 before ours.)
- [x] C. (same merge; caddy reloaded 09:56 UTC, sitemaps 200 application/xml, probes still 404, /api/stories 200 on both) Caddy: real /sitemap.xml per app host (was the SPA shell with a 200), robots points at it.
      Same branch; needs a caddy reload on the box outside the nightly windows.
- [x] D. (merged 938bb3d, live 10:14 UTC) Orbital app SEO: canonical/OG/Twitter/JSON-LD Dataset in web/index.html, per-route
      titles + descriptions + canonical, og.png. Branch space seo/orbital-20261019 (subagent).
- [x] E. (merged exodossier 240daf6, live 10:08 UTC) Exo app SEO: same shape. Branch exodossier seo/exo-20261019 (subagent).
- [x] F. Independent review of each branch, merge, deploy (landing = git pull on the box; apps =
      image rebuild), live verification incl. /api/stories on both hosts.

## Review (2026-10-19 10:20 UTC, nav + SEO batch)
Nav: vibcreates.com's "the work" link is a "projects" disclosure listing CannMenus, Orbital,
ExoDossier and SplitStep directly, each with a sentence and a jump to its write-up; the phone
menu leads with the same four rows. The review caught that showing the panel on :hover and
:focus-within let aria-expanded disagree with the screen and kept Escape from closing it; now
.open is the only thing that shows it and one setter drives click, hover (fine pointers only),
Escape and focus-out. Verified by Playwright: keyboard, mouse, touch, 901 to 1440 px.
SEO: share and structured-data tags on all three landing pages (projects typed WebSite, not
WebApplication, which Google reports as broken software-app items); Caddy serves a real
application/xml sitemap per app host (was the SPA shell with a 200) and robots points at it;
both apps now rewrite the shell's head server-side per path from one table
(web/public/seo-routes.json, read by seo.ts and api/seo.py), so deep links carry their own
title, description, canonical and og:url to scrapers that never run JS, and an unknown path is
a real 404 with noindex. Live checks: exo /target/1 200 with its own canonical, /nope 404,
/LightCurves/ canonical lowercase without the slash; orbital /buses/<slug>?kind=bus keeps the
kind param in its canonical (slugs collide across kinds), /Resolver/25544/ normalises; /api/stats,
/api/stories, /api/health, lightcurves processed 58, all unchanged; box 35%.
Not done: prerendered per-route bodies (only heads are per-route); the share cards are
screenshots dated 2026-10-19 and will drift; Search Console verification token still blank.

# Look-and-feel and consistency batch, 2026-11-04 (Vib: "keep going … check for bugs,
# check for inconsistencies … make it really, really good")
Owner: session faraday. Both apps built in fixture mode and screenshotted on every route at
1440 and 375 px (no console errors, no horizontal overflow on either app before or after).
- [x] A. Filings was the one view without the chapter head: it opened on "Pl. 01" with an
      unstyled native input (`className="input"` has no rule in theme.css). Now the standard
      .vhead title + description, the Resolver's .searchbar (Enter submits), and the pipeline
      copy moved up into the description.
- [x] B. Deep links title the tab by entity, as exo's Target view does: "ISS (ZARYA) · Resolver",
      "SpaceX · Operators", "Starlink V2M · Bus Benchmarks", "cospar:1966-116B · Review"
      (seo.ts useDocumentTitle; the route table still feeds the share-card tags).
- [x] C. Visitor-facing fetch errors: a 404 / 429 / 5xx / other 4xx no longer shows the raw
      "404 Not Found for /satellite/1" line (exo's mapping ported, with the API's own detail
      text passed through for a 4xx that has one), and a 2xx body that is not JSON reads as
      "answered with something that is not data" instead of the parser's words.
- [x] D. Verified, no change: the Review case's verdict bar looked to cover Pl. 05/06 in
      full-page captures, but it is position: sticky and sits in flow once reached.
Gates: `pnpm build` clean (tsc + vite); `ruff check .` and `pytest -m "not db"` unchanged
(275 passed). The db suite needs TimescaleDB and could not run in this container.
- [x] E. The SPA had no automated tests and CI never built it: vitest (3.x, the line that runs on
      Vite 5) with 20 unit tests over lib/format, seo (route table vs App.tsx, canonical rules)
      and States.visitorMessage; a `web` CI job runs them and then tsc + vite build.
- [x] F. README: the terminal section listed four views; it lists the eight.
- [x] G. Web audit (an independent read of every view, component and helper; 25 findings, each
      re-verified in the code before the fix): the Review case's window Enter handler swallowed
      Enter on every button and link (Next, Queue, the guide, the nav) and ran submit instead;
      a nested receipt link's Enter bubbled to the clickable row and opened the cohort instead;
      sortable column headers were click-only (now real buttons); the ownership timeline drew
      "transfer" marks from operator and manufacturer segments on the owner track and labelled an
      open-ended record "2028" from the padded geometry (now "present"); "Status history ·
      latest first" was API order; the "catalog says active, physics says decaying" tile linked
      to Conflicts, a different dataset (now a figure); the tour forced itself after 3 s with its
      anchors unmounted (now waits for both anchors, gives up quietly, cancels on unmount); the
      heatmap caption counted undrawn bins and told visitors to "click"; toggle groups carried no
      aria-pressed; a link sat inside the Filings row button (now a div with an explicit details
      toggle); Review case storage access could throw in render; a dossier-seeded verdict stole
      focus on load and disabled the shortcuts; a failed next-case lookup after a stored verdict
      read "Submit failed" and a retry would 409; receipt links dropped the bus kind; an aliased
      slug showed the survivor's figures without saying so; older snapshots rendered "NaN%";
      "No all cases", a silent Start-reviewing, the queue tab lost on the way back; compact()
      gave "1000K"; fmtDuration gave "6m 60s"; tab changes fetched the old page offset first;
      three /api/stats calls per landing (now one in flight); an empty launch series crashed the
      Overview; positive drag medians clipped and null storm days vanished from the strip; a
      ?kind=bus deep link left the Manufacturers tab selected.
- [x] H. API audit (an independent read of both services, several findings reproduced through
      TestClient with a stubbed database): /api/satellites/search?q=² answered 500 because
      str.isdigit() accepts "²" and int() rejects it (ASCII digits only now; LIKE metacharacters
      in a name search are literal); a non-ASCII X-Review-Token raised TypeError inside
      hmac.compare_digest (a 500 instead of a 401: compared as bytes now); page offsets above
      int8 reached Postgres as NUMERIC and failed (bounded); the server canonical for a param
      with a space or quote carried it raw while the client's carried the percent-encoded form
      (percent-encoded on both sides; the escaping test moved with it); a matched route marked
      noindex would have shipped a canonical (served without one and with the robots tag);
      orbital sent no CORS headers while exo allowed any origin to GET (the same read-only
      policy now); the README and the app description called the service read-only while the
      token-guarded verdict route writes (they say so).

- [x] I. Adversarial review of the branch itself (three independent refuters per finding; what
      survived, orbital's share): Enter after a mouse click on a verdict button re-selected it
      instead of submitting (the clicked button keeps focus in Chromium, and the new control
      guard treated it as a control; verdict buttons are exempt); the corrected-answer autofocus
      tracked a mount flag that StrictMode's effect replay defeated (it tracks the previous
      verdict now); the sort button fills its header cell so the whole cell is the hit area
      (44 px tall on touch screens); the pager formats en-US like every other number; a ?kind=
      flip that arrives by URL resets the leaderboard page and sort the way the toggle does; a
      failed "Start reviewing" reads as visitor words; a non-2xx answer carries the API's own
      detail when it gave one; the server canonical mirrors the browser's pathname encoding
      (the WHATWG path set leaves + ' ( ) , : @ bare), with a test. Refuted and left alone: the
      `only` carry in the review nav, grid rounding in the heatmap, the "20 hits" cap wording,
      row links on the league tables.
- [x] J. Design pass (24 items from a ranked plan scored by two reviewers for look, feel and
      consistency across both apps, in three batches; then an independent verifier measured
      every item against a build of the base commit, two fix rounds closed what it found, a
      parity pass aligned the shared behaviour with ExoDossier, and one last fix closed the
      verifier's final finding; everything measured with Playwright at 1440, 1024, 390 and
      360 px; the final sweep of 17 routes x 2 widths has scrollWidth == clientWidth, no
      console errors and one h1 per page). One name: the site is "Orbital Economy Terminal"
      on the plate, in the colophon, in every route title, the 404, index.html and the
      JSON-LD; the project stays "Orbital Economy Intelligence". Lists reload in place:
      useApi({ keep, keepKey }) keeps the paged lists mounted and dimmed while a page, sort or
      search loads (focus and scroll hold), a tab that picks another list shows the loader
      (a kept page had rendered under the new tab's columns), a failed reload's Retry shows
      the loader, the pager's end buttons are aria-disabled so landing on page one keeps the
      focus, and a page past the end reads "Nothing on this page" instead of "41–40 of 214"
      (8 vitest cases). Phones: Filings rows wrap instead of clipping the spec chip; the
      ownership timeline fits (the transfer tag ran 17 px past the page) and narrow segments
      show years through a container query, ellipsis last; the verdict bar is a 2x2 grid of
      44 px buttons without the legend or the key caps and says "Pick a verdict." on touch;
      the sr-only stories table no longer widens the page; tab strips fade where they scroll
      (to 1024 px, where the rail leaves a 615–714 px column); the congestion heatmap fits
      its cells to the panel on phones (6–7 px), keeps the designed 12–13 px on desktop and
      marks itself scrollable with a fade below that; focus rings draw inside their scroller
      and the pinned first cell paints its share; inputs are 16 px. Tablet: panel heads wrap
      to a one-line title over a right-aligned meta at 1024; the resolver detail grid goes to
      one column; the space-weather row is three columns. Type: hero counts are Fraunces like
      every stat value; "Under the hood" steps to 19 px; display titles on a 1.15 line-height;
      72ch / 48ch measures; the 10 px classes are 11 px; closing notes share one rule. Honest
      states: disabled controls are legible (5.2–5.5:1, were 2.0–2.8:1); waiting controls
      stay enabled with aria-busy so focus never falls to body; the review case head and its
      nav are hoisted above the loading case; the life-track placeholder is the chart's own
      block, so the Q&A panels no longer jump when the history lands (148 px before, 44 px on
      phones after the first fix, 0 now); the coverage meter's low band is the error red, not
      the status rust; storm bands are signal blue, not conflict amber. Copy: the case h1 is
      the case type with the subject beside it; "7 ids · by source", "1 id"; the search field
      says what it takes; no-match states quote the query; "Schedule S tech report PDFs",
      "docket 9 of 28 pending"; full measure names on bus sort buttons; "cataloged",
      "observer", "Bus benchmarks"; "Jul 2024" on the race axis; the Kp band in the
      space-weather sub; the landing description says what the page shows.
- [x] K. Code review of the design diff (six readers per repo — state logic, markup and a11y,
      CSS cascade, copy and docs, tests and gates, API and shell — each measured in the
      browser; 43 findings for this repo, hand-triaged, 41 fixed). Keyboard: a list reloading
      in place was inert for the mouse only, so Enter on the pager kept paging through the
      request (the kept wrapper swallows clicks while it loads); the case h1 was the stratum
      alone, so every case in a stratum had the same heading (the subject rides inside it now);
      the live region announced the keyboard legend on every submit (only the error is live);
      the legend hid under 560 px while the key caps hid on a coarse pointer (both on touch
      now); resolver result rows drew their ring outside their scroller; the search bar rang
      for the cohort chip inside it; a control scrolled under a pinned column stayed hidden on
      focus. Lists: Buses and Review got the past-the-end page and a no-match state (the board
      showed a header-only table and "0–0 of 0"); Filings' debounced commit read the
      keystroke's params and re-applied a cohort chip cleared during the pause, its kept page's
      empty copy named the filter still loading, and "clear search" dropped the focus to the
      body. Phones: the 320 px verdict bar ran the notes field under Submit; inert tabs painted
      a 2px bar in the active slot; a busy button fell to 3.3:1; go/submit buttons stayed 36 px
      on touch; label chips got button padding. Copy: one name for the GP coverage metric and
      the methodology document; "present" on the short timeline; "1 id"; the Filings
      methodology meta links the document it named; the landing's JSON-LD, the README
      screenshot caption and "catalogued" in two docs and the audit report. Tests: the static
      shell is held to the route table on both sides (vitest reads index.html; the dist-backed
      Python test reads GET /); the pure seo tests read the source table; the landing
      description is a relation, not a prefix; new tests for the Kp band, inert pager ends, the
      shared life-track foot, coarsePointer, the Filings copy and the resolver result states
      (45 vitest cases). A second wave from the ExoDossier readers, mirrored here: while a kept
      page reloads the pager printed a range true of neither the rows on screen nor the page in
      flight (it names the page in flight now), Next stayed live on a page past the end, and a
      failed page turn dropped the focus to the body (Retry takes it). Found on the sibling app
      and shared here: the colophon's touch tap boxes lay over the next line's links (a tap
      could open the other link; the box pushes the lines apart now) and its dotted rule was
      1.5:1 (5.5:1); a tab or view link focused by keyboard scrolls clear of its strip's fade;
      the pager's range is a status region; one past-the-end predicate for the pager and five
      views; the 16 px control floor follows the device, not the width; GET / was the one shell
      served verbatim from web/dist with no cache header (Starlette normalises the root to
      "."), so it renders like every route now and every rendered shell is no-cache. Left: hook
      tests that would need a DOM environment, the "Submit ⏎" glyph on touch (the soft
      keyboard's return does submit), and the API's "Bus Benchmarks methodology" document
      heading on GitHub.
- [x] L. The 404 page opens with the chapter head like every other view (it was an empty state
      with no h1, the one route whose heading outline differed): "Page not found" as the h1,
      the path in the description, the two ways back beneath.

# Stopping point, 2026-11-11 21:00 UTC (space-ed; Vib: "get to a good stopping point with next steps clearly documented")

## Done today
- [x] PR #1 in both repos (the cloud session's review, design and code-review passes, merged on
      GitHub on 2026-11-04) was merged but never deployed; the box ran the 2026-10-19 code.
      Gates first: orbital build, 47 vitest, 284 pytest passed (309 skipped, the db-dependent
      ones); exo build, 41 vitest, pytest green. Deployed 20:38 UTC (space 964752d, exodossier
      ee95827); no migrations. Live: titles read "Orbital Economy Terminal", deep links 200 with
      their own titles, unknown paths 404, every API endpoint 200; a headless pass over 8 routes
      at 1440 and 390 px found one h1 each, no horizontal overflow, no console errors.
- [x] Favicons (space b717c92, exodossier ef45e6d): neither shell declared an icon, so tabs
      showed the browser default and every first visit logged a 404. SVG marks in each app's
      own colours, live and served as image/svg+xml.
- [x] The retired ledger `source_assertion_runs_20261012` dropped (see the 2026-10-12 section):
      5 GB freed, box 39% -> 33%.
- [x] Space-Track live-night criteria checked against production (spec updated): green every
      evening since 2026-10-16, no errors, about 130 requests a day.
- [x] The merged `space-wt-claims` worktree and its branch removed.

## Health at the stopping point
Both nightlies exit 0 on every step (19:10 run 2026-11-11 finished 19:27). Box 33% disk,
backups nightly (newest 2026-11-11 02:43 UTC). Containers rebuilt 20:51 UTC.

## Next steps, in order (all five done 2026-11-12 by space-ed, Vib: "Yes let's do those things")
- [x] 1. **Space-Track decay -> resolver wiring.** Migration 0028 + identity/claims.py: every OK
      decay landing is folded into `spacetrack_decay_message` (each message ever seen; a message
      is withdrawn only when a newer landing's window covers its epoch), and the best surviving
      message per object is the `spacetrack_decay` snapshot feed, joined through the SATCAT NORAD
      link (a second v_claim branch; timed on prod, within noise). Two independent reviews: the
      first cut ("windowed" feed) let an aged-out better message lose to a worse one and could
      never retract a withdrawal; rebuilt as above. 12 tests, five mutants caught. Rolled-back
      dry run on prod: 626 objects claimed, 133 displayed decay dates change (80 by one day).
      Deployed 15:16 UTC (space 440adb7, migration applied); the first fold runs in the
      2026-11-12 19:10 nightly.
- [x] 2. **Share cards** retaken from the live sites (tour suppressed), deployed (space fb30603,
      exodossier f87c5c0). The landing card has no dated numbers and was left alone.
- [x] 3. **Box cleanups**: `zz_bus_before`, `zz_gcat_links_before` dropped (no dependents).
- [x] 4. **Prerendered bodies.** A site: search found neither app indexed, and AI crawlers run
      no script, so each route's server-rendered shell now carries its h1, description and links
      to every section inside #root; React clears it on first render and the shell hides it
      while script runs. Reviewed (no important findings; minors fixed), checked in Chrome with
      and without JavaScript, live (space 763471b, exodossier 5975c43).
- [x] 5. **Stray branch** `codex/audit-followup-20260917` merged (September verification notes)
      and deleted locally and on origin.

## After 2026-11-12, open
- [x] First Space-Track fold verified after the 2026-11-12 19:10 nightly: every step exit 0;
      1,008 messages folded (181 superseded), 631 claims opened, all 631 reach a satellite and
      all 631 satellites show Space-Track's date; /api/satellites lists it beside SATCAT and
      GCAT; /api/conflicts/decay total 4,332.
- [x] The prune could delete a decay landing a failed build_graph never folded: guarded
      2026-11-12 (FOLDED_BY in scripts/prune_snapshots.py; space a033089, deployed; prod dry
      run clean). README now says the full suite needs `make metrics` as well as the migrations.
- [x] Status lag after the wiring: 628 of the 631 Space-Track-decayed satellites read DECAYED,
      3 PARTIAL (decayed 2026-11-11). No Space-Track status source needed.
- [x] IndexNow (Bing, Yandex and the other IndexNow engines; ChatGPT search reads Bing's index):
      key file `8772f058e23a0521b19e03d2f3b9b89c.txt` at the root of all three hosts (deploy/landing, web/public in both
      apps), and every sitemap URL submitted 2026-11-12 20:51 UTC: 3 + 8 + 6 URLs, all 202.
      Resubmit after a sitemap change with a POST to https://api.indexnow.org/indexnow
      ({host, key, keyLocation, urlList}). Google does not take IndexNow; Search Console is
      still Vib's.
- Note: exodossier CI lists a run for ef45e6d (2026-11-11) as queued with no jobs; GitHub says
  it is completed when asked to cancel. A ghost entry; later runs on the same code are green.

## Still Vib's
- [x] Google Search Console, done 2026-11-22 at Vib's ask: the three hosts were added as URL
  prefix properties and verified by HTML tag (one token for the account, in all three
  index.html files: space 733f4b9, exodossier e5f59f0), and each sitemap.xml was submitted
  (3, 8 and 6 pages discovered). Keep the tag in place, or verification lapses.
- [x] The untracked `page.html` (a saved OpenHands marketing page from 2026-10-15, left over from
  the job search) was moved to the Trash on 2026-11-12 at Vib's OK.
- [x] Space-Track open questions, settled 2026-11-19 at Vib's go: the July gap backfill is not
  run, and the "reentering soon" pull has a draft spec (docs/specs/spacetrack-reentry-predictions.md)
  waiting for Vib's read, with three questions for him in it.
- [x] Reentry predictions phase 1 built 2026-11-22 at Vib's "proceed however you think best is
  recommended": the three questions closed on the recommendations (its own weekly allowance, an
  Overview panel, no TIP yet). Migration 0029, the weekly pull in the nightly top-up, GET
  /api/reentries/upcoming and the "Reentering soon" panel; Codex verify pass and a review
  workflow applied (spec decision log). Deployed d98a45d at 04:53 UTC on 2026-11-22, migration
  0029 applied; the panel stays hidden until the first pull lands.
- [ ] After the Wednesday 2026-11-25 19:10 UTC nightly: one OK decay_60day run, the
  "reentry predictions:" summary line without "!!", the Historical pull and the claims counts
  unchanged from the night before (baseline 2026-11-22: 315 actual-decay rows, 704 open
  spacetrack_decay claims), /api/reentries/upcoming 200 in under 1 s, and a real sample
  recorded as the test fixture (spec acceptance criteria and open questions).
- Codex review is paused (subscription ended 2026-10-21); reviews use Claude reviewer agents.
- [x] **UCS is not in production** (never ingested: the loader is an optional seed and the box has
  no UCS file), yet the copy claimed it. Settled 2026-11-12 at Vib's "proceed how you see fit":
  the copy now names only the sources in production (SATCAT, GCAT and Space-Track decay
  messages) on the Overview, the route descriptions, the JSON-LD, the vibcreates.com entry and
  the rail's provenance key. The loader and the precedence slots stay. Loading the frozen May
  2023 export remains possible later; it would change thousands of names, so measure on a copy.
- [x] Product call on NORAD 48587, settled 2026-11-12 with a narrow rule (spec decision log): a
  Space-Track message is set aside when SATCAT and GCAT agree to within a day and the message is
  more than 30 days off. On production that is 48587 alone, a one-year typo. Precedence is
  otherwise unchanged and the claim stays on /conflicts.

## Next session (paused 2026-11-12 23:58 UTC for a terminal restart)
Nothing is running: no workflow, no background job, no uncommitted change in either repo.
- [x] After the 2026-11-13 07:10 UTC nightly, NORAD 48587 resolves to a decay date of
      2025-01-07 (`SELECT decay_date FROM satellite WHERE norad_id = 48587` on the box, and its
      resolver page). The rolled-back dry run on production changed exactly that one row. The
      nightly summary now prints "decay messages outvoted: 1 (NORAD 48587)" in refresh.log
      (space e09c308), so check that line as well as the row.
      Checked 2026-11-15: /api/satellites/48587 reads decay_date 2025-01-07, and every run's
      summary since has printed "decay messages outvoted: 1 (NORAD 48587)".
- [x] Independent review (Claude reviewer agents; Codex is paused) of the merges that have not had
      one: 7e3a8ca (the outvote rule), 4907f3c (UCS out of the copy) and a033089 (the prune
      guard). Lenses: the rule's correctness, every consumer of decay_date (the Conflicts page,
      the stories, the review cases), and any UCS claim left in either app, the landing or the
      methodology text.
- [x] Spec reconciliation. These open criteria look shipped but were never checked off. Verify
      each against production, then check it off with evidence or do what is left:
      landing-stories.md (10, the same list in both repos), gcat-sibling-links.md (4),
      raw-retention.md (2; the second may be moot since the ledger was retired),
      lightcurves-wave2.md in exodossier (1), and items 6, 7 and 8 under "Needs a decision from
      Vib" plus the Space-Track account line at the top (creds have been set since 2026-10-16).
      fcc-filings.md has one real deliverable left, the changelog draft in Vib's voice.
- [x] Copy-truth audit of vibcreates.com, orbital and exo: check every factual claim (sources,
      numbers, refresh cadence) against the live API, since the UCS claim showed that this kind
      of defect exists.
- Found while scouting, no action needed: the API never computes a winning source from
  precedence.yml (satellites.py mentions it only in a docstring), so the outvote rule cannot
  make the API disagree with the card. scripts/review.py still lists ucs in SOURCE_ORDER, which
  only orders review cases. OpenSEO holds only the Mattress Central project, so it has no
  Search Console data for vibcreates.
- Planned shape (ultracode is on): one workflow with three independent branches (review, spec
  reconciliation, copy audit), read-only on the box with a statement timeout, and every finding
  adversarially verified before anything changes. Box work stays out of 07:10 to 07:25 and
  19:10 to 19:25 UTC.

## Review (2026-11-13 02:25 UTC, audit batch)
- One workflow ran the review, the spec reconciliation and the copy-truth audit; 75 findings,
  66 confirmed by two verifiers. Spec bookkeeping landed as space 9654738 and exodossier adb752d
  (21 criteria and 4 todo items ticked with dated evidence).
- Fixes merged and deployed in five groups: space e09c308 (resolver follow-ups with the
  outvote list in the build summary and `normalize.has_day`, Overview stats and copy, the
  Kuiper card on DA 26-553 with 1,616 required and 392 in orbit on the deadline, filings lead
  times, the landing copy) and exodossier beb4530 (the follow-up conflict flag from the
  disposition corpus, the TFOPWG column, attribution and licence copy).
- Second batch: space d1dec6c (refresh cadence on the landing, sitemap lastmod, 94.5%
  both-catalog coverage in the README, the deploy README's prune guard, the 1,616 constant and
  a dated correction in the July report) and exodossier 32f48d1 (no "first exoplanet MCP server"
  claim, catalogs named as loaded). Space ff10219 retook the share card and the four README
  screenshots from the live site; og:image carries `?v=20261113`.
- IndexNow resubmitted 2026-11-13 02:20 UTC: 1 + 8 + 3 URLs, all 200.
- Every page and API on the three hosts answered 200 after each deploy.
- Subagents hit the weekly limit (resets 2026-11-17); the last reviews ran inline.
- Left for Vib: the GitHub repo descriptions (OEI says "four catalogs", exodossier says
  "first exoplanet MCP server"); an explicit `shm_size` for the db container (DONE 2026-11-19
  00:32 UTC at Vib's go: shm_size 1g in 7f1e1c0, db recreated, healthy in 12 s, both APIs 200); the space-weather view dominated by Starlink (needs a DROP MATERIALIZED VIEW);
  ISS-deployed lifetimes in bus_benchmarks.sql (a methodology bump); the decay predicate in
  v_congestion_exposure; "≥2 identifiers" in quality/report.py; 33 of 34 famous exo hosts
  flagged disputed on Follow-up (a product choice); NORAD 56915 with a stale decay date and
  fresh elements.

## Review (2026-11-13 21:15 UTC, congestion exposure and the decay phase-date fix)
- Congestion exposure is live on every operator's panel (space 1db82fd, spec
  docs/specs/congestion-exposure.md, now done). GET /api/operators/{id} carries a `congestion`
  block from v_congestion_exposure, built for all operators in one 1.1 s query and kept in the
  warm cache. SpaceX on production: each object shares its shell with about 2,894 others, 2,755
  of them its own; the shell holding the most of its objects is 450 to 500 km at 50 to 55
  degrees, 4,057 of 4,177.
- Review: a three-lens workflow (17 agents, every finding put to a refuter) left ten defects,
  all in copy and tests; a fresh reviewer then took three more rounds, the last a 400,000-case
  fuzz, before the sentences held. Lessons are in the spec's decision log: round the parts by
  largest remainder, say "all" or "none" only of a true 0, give tenths below 1 per object, and
  seed tests so the wrong formula gives a different answer (three mutants checked).
- Codex is still unavailable: the account is on the free tier with a usage limit until
  2026-11-16 (memory note codex-cross-provider-review updated).
- The peer session's decay phase-date fix is merged and live (6bd067b): decay conflicts 4,334
  to 3,523, conflict rate 12.0% to 10.1%, landing "3,700+" with the snapshot baked twice (the
  second after the deploy, conflicts 4,539 to 3,728). Verified live: /api/stats decay 3,523,
  OGO A (879) in none of the 3,523 rows, /api/satellites/40452 without a decay_date conflict.
- Found while measuring, for Vib (no change made):
  - Four objects carry a decay date older than their latest element set. NORAD 56915 (Starlink,
    GCAT says reentered 2026-05-07, SATCAT operational, elements at 482 km on 2026-11-12) is a
    GCAT error. 44547 Yunhai-1 02 and 24946 Iridium 33 are GCAT status C ("collided"), which
    status_map.yml maps to DECAYED although both parent bodies are still tracked. 65077 Dragon
    C206.6 is a SATCAT decay with a later element set and looks benign. A rule such as "a GCAT
    decay date is contradicted by an element set more than 30 days after it" would fix the first
    three; it is a resolver methodology change, so it waits for a decision. None of the four
    leaves the congestion field, whose predicate only drops an object decayed on or after its
    latest epoch.
  - 35 objects have a decay date while SATCAT reports them in orbit with no decay date; most
    have no element set to settle it either way.
  - The review queue on production still holds decay_conflict cases built from the old top 20,
    which were nearly all GCAT phase dates. Removing them is a database write.
  - [x] The satellite card still lists a GCAT phase-end date under decay_date with no label.
        Done 2026-11-18 06:16 UTC (space a55e88f, Vib: "go ahead and merge and deploy it"):
        GET /api/satellites/{id} marks the claim with phase_end, and the card prints "end of a
        phase, not a decay" beside the value. Verified live on 40452 and 879; PS-1 (a real
        decay disagreement) carries no mark; every page and API answered 200 after the rebuild.
- After the 07:10 UTC nightly on 2026-11-14: check "display name overrides: 8", "unmapped
  status values: 0", /api/satellites/40452 without a status conflict, and the ingest retry log.

## Review (2026-11-14 21:20 UTC, post-nightly check and GCAT's extended catalog)
- Post-nightly checks for 2026-11-14 all pass. Both runs (07:10 and 19:10) exited 0 at every
  step, with "display name overrides: 8" and "unmapped status values: 0".
  /api/satellites/40452 now shows only a name conflict (its status conflict cleared). Neither
  run needed a retry: every pull succeeded first time.
- Found during the check: GCAT's S catalog split in July when NORAD passed 99999.
  satcat.tsv stops at S69999, and satcat100k.tsv holds S100000 onward. We pulled only the
  first file, so every GCAT run since 2026-07-21 landed exactly 69,999 rows and reported ok.
  - 1,309 satellites (NORAD 100000 to 101357) had no GCAT owner, status, bus or manufacturer.
  - The Starlinks among them were attributed to "United States" through SATCAT's country code.
  - The spec is docs/specs/gcat-extended-catalog.md.
- Fixed and live since 21:12 UTC (space 23d63cd, Vib: "Deploy tonight").
  - Both files of each catalog land in one run, and a run with a failed, empty or non-catalog
    part closes error.
  - The build summary prints "gcat coverage:", counted against the newest GCAT snapshot, and
    starts it with "!!" (forwarded to ALERT_URL) above 1%.
  - The ISS release-date lookup builds six-digit JCATs.
- Review: three lenses with a refuter each (6 agents), then a fix-round reviewer.
  - Confirmed: a part answering 200 with only its header would have closed 7,999 claims while
    coverage read 100%, because the first coverage line counted links, which never expire.
  - Confirmed: the duplicate check read the key column by position.
  - Confirmed: a test that passed by construction.
  - All are fixed, and the coverage test now fails on six SQL mutants.
- Vib decided "leave it for now" on GCAT's Hawk 16A typo. S101146 carries Satcat 100146, so
  satellite 100146 (a Chinese object) will read "Hawk 16A", owned by HawkEye 360.
- Expected at 07:10 UTC on 2026-11-15: one alert, "!! gcat coverage: 98.2% (69999; 1309
  without ...)". GCAT is still fresh then, so part 3 is first pulled at 19:10.
- After the 19:10 UTC nightly on 2026-11-15 (not before 19:30):
  - gcat_satcat lands about 71,350 rows and gcat_psatcat about 29,100, both ok.
  - The coverage line reads above 99.9% with no "!!", and NORAD 100000 and up has 2 or fewer
    satellites without a GCAT id.
  - About 46 satellites are added; every step exits 0; the slug gate and publish assertions
    pass.
  - Spot-check SpaceX's fleet (it should gain about 850), "United States", and NORAD 100146
    ("Hawk 16A").
  - Re-bake the landing snapshot (deploy/landing-snapshot.sh), commit "Landing: snapshot
    2026-11-15", and run check_landing.
  - Compare /api/stats conflicts with the public copy: the README says 3,523 decay
    conflicts, and the landing says "3,700+" substantive conflicts. Part 3 adds GCAT
    status and decay claims for 1,307 satellites.
  - Tick the spec's production criterion.

## Review (2026-11-15 19:55 UTC, GCAT part 3 on production)
- The 07:10 UTC run sent the one expected alert ("!! gcat coverage: 98.2% ... 1309 without")
  and nothing else.
- The 19:10 UTC run pulled part 3. Every step exited 0, no alert was sent, and the slug gate
  and publish assertions passed.
  - [x] gcat_satcat landed 71,400 rows and gcat_psatcat 29,157, both ok.
  - [x] The coverage line reads "gcat coverage: 100.0% (71397; 0 without)", so no satellite at
        NORAD 100000 and above lacks a GCAT id.
  - [x] Satellites grew by 89, not about 46. Of those, 16 are new in CelesTrak's SATCAT and 46
        are the classified USA 726 to USA 771. The other 27 are Starlinks and a Starship (NORAD
        101358 to 101384) that GCAT catalogued before CelesTrak. SATCAT upserts on norad_id,
        so they join SATCAT's record when it lists them.
  - [x] SpaceX holds 611 satellites at 100000 and above, 606 of them Starlinks, not the 850 or
        so forecast. That forecast misread "most of SATCAT's 853 US rows"; only 580 of those
        were Starlinks. "United States" holds 2 there.
  - [x] Hawk 16A: GCAT fixed the typo upstream. 100146 reads "Xiguang 2-03" (Xinghuo
        Chuanming), and 101146 reads "Hawk 16A" (HawkEye 360). The open question is closed,
        and Vib does not need to contact GCAT's maintainer.
  - [x] Public conflict figures still hold: 3,523 decay (README) and 3,728 in all (landing
        "3,700+").
  - [x] Landing re-baked and live (space 544ec19): 71,397 satellites and 13.6M element sets.
        check_landing passes.
  - [x] Spec docs/specs/gcat-extended-catalog.md: production criterion ticked, status done.
- [ ] After CelesTrak lists NORAD 101358 to 101384, confirm they stay one satellite each and
      the build exits 0. The upsert means this should need no action.
      2026-11-18 04:35 UTC: still unlisted. CelesTrak reaches 101410, but its new numbers are
      other objects (launches 2026-257 and 2026-258 and debris), and no COSPAR is held by two
      satellites. The five nightlies from 2026-11-15 19:10 to 2026-11-17 19:10 exited 0 with no
      alert; the last read "gcat coverage: 100.0% (71399; 10 without)", ordinary GCAT lag.
      2026-11-18 06:30 UTC: still unlisted (0 of the 27 in raw_satcat), still 27 satellites
      for 27 numbers.
- [x] Two Codex Medium findings fixed (space 62771ec, CI green on 1014481): the stories
      release-date lookup takes the newest GCAT row first, and a failed multi-part pull records
      the bytes it downloaded. Neither changes production output today (the one key whose newest
      row dropped its NORAD, S66057, is not an ISS piece). Deployed 2026-11-18 06:38 UTC
      (oei-api rebuilt at f3cca7f; both changes confirmed inside the container, sites 200). The test cleanup scope stays as recorded in the spec.
- [x] 2026-11-19 00:32 UTC: db recreated with shm_size 1g (space 7f1e1c0). Parallel queries had
      hit "could not resize shared memory segment ... No space left on device" on 2026-11-13
      with Docker's 64 MB default. /dev/shm now reads 1.0G; both nightlies of 2026-11-18 had
      exited clean beforehand (19:10 landed 71,410 GCAT rows, coverage 100.0%).
- [x] 2026-11-19 00:43 UTC: GET /api/operators 12.7 s -> 1.2 s (space a7dbafe, deployed). The
      league read latest status with a DISTINCT ON over 4.95M history rows, twice per request;
      it now reads mv_latest_satellite_status, identical counts for all 1,480 operators.

## Review (2026-11-20 03:10 UTC, a decay date the tracking contradicts; stale review cases)
- Vib, 2026-11-19: "Yes sounds good go for" on the resolver rule and on removing the stale
  review cases. Spec: docs/specs/decay-tracked-after.md. Live as space 6f5a8b8.
- The rule: a decay claim is set aside when the object's latest element set is dated more than
  30 days after it, and that source does not vote DECAYED for the status. Its earlier DECAYED
  rows in satellite_status_history are deleted in the same pass (a Claude reviewer found that
  the old row would otherwise stay the newest; Codex is still paused).
- It sets aside 12 GCAT claims. Three change the card: 56915 (GCAT's reentry is really
  59615's, entered under the transposed number), 44547 and 24946 (collided, parent body still
  tracked). Nine are GCAT dates a year early that SATCAT already outranks.
- [x] After the 07:10 UTC nightly on 2026-11-20: refresh.log has "decay dates set aside,
      tracked after: 12" and "decay messages outvoted: 1"; the cards of 56915 and 44547 read
      ACTIVE with no decay date, 24946 reads INACTIVE with none; 65077 and 45699 are unchanged.
      Checked 2026-11-22 04:00 UTC against the 2026-11-21 19:10 run: all of it holds.
- Review queue: 10 of the 20 decay_conflict cases were GCAT phase dates (case ids 187, 188,
  189, 190, 192, 193, 195, 201, 204, 206). Deleted on production with their dossiers at 02:56
  UTC; backup at /root/backups/gold_case_decay_phase_dates_20261120.json on the box. The other
  10 are real conflicts and stay. `python scripts/build_gold_queue.py --type decay_conflict`
  would draw the stratum back up to 20; not run, since it is a further write.
- README: the decay-conflict count now reads "about 3,500".
- Still open for Vib: which key wins when a satellite holds two for one source (the resolver
  takes the greater, the readers and the card the lesser); whether to report the GCAT typos.

## Review (2026-11-22 04:10 UTC, post-nightly check, one key pick, review queue refilled)
- Vib, 2026-11-21: "proceed however you recommed is the best option forward".
- The tracked-after rule held on the nightlies: refresh.log reads "decay dates set aside,
  tracked after: 12" and "decay messages outvoted: 1 (NORAD 48587)". 56915 and 44547 read
  ACTIVE with no decay date, 24946 INACTIVE with none; 65077, 45699 and 59615 are unchanged.
  The spec (docs/specs/decay-tracked-after.md) is done.
- One key pick (space 3622386, live 04:04 UTC): the decay-conflict readers, the satellite card
  and the review queue's evidence now take the greater key of a two-key satellite, as the
  resolver does. No satellite holds two keys of one source with a claim today, so no count
  moved. Deploy order was agreed with the session building reentry predictions (vgupta-85),
  which deploys after rebasing on this main.
- Review queue: `build_gold_queue.py --type decay_conflict` run on production at 04:05 UTC. The
  stratum is 20 again: the 10 kept cases (case ids 191 to 205, dossiers intact) and 10 new ones
  (636 to 645, no dossiers yet). All 20 are real SATCAT against GCAT disagreements.
- GCAT: its file as published on 2026-11-22 still carries the ten entries listed in the spec
  (nine DDates a year early, and S56915 holding S59615's reentry). A short note to GCAT is
  drafted for Vib; not sent.
- [x] Vib: send the GCAT note, or leave it (GCAT fixed the Hawk 16A typo on its own).
      Sent 2026-11-22 21:00 UTC (Vib: "yes go for it", then he approved the final text and the
      sender address in a dialog). From vibhavgupta2@gmail.com to planet4589@gmail.com, the
      address jsr.html publishes for corrections; subject "GCAT satcat: ten DDates that
      disagree with tracking". An eleven-agent check first found that the draft overstated:
      only five of the nine are a pure year slip, and the SATCAT in question is CelesTrak's.
      The note offers a list of seven more rows (six confident); it is in the spec
      (docs/specs/decay-tracked-after.md) if he asks. A reply would arrive in the vgupta998
      inbox; search `from:planet4589@gmail.com` only.
- [ ] If GCAT fixes the rows, the nightly's "decay dates set aside, tracked after" count drops
      below 12 by itself, and 56915 stops carrying a GCAT decay claim. Nothing to change here.
- [ ] S63359 and S63360 (Electron 63 Kick Stage and Stage 2) look swapped between GCAT and
      CelesTrak. Not reported; needs the element-set history of both first.
- [ ] The 10 new decay cases have no research dossier. The dossier pass is a separate job.
