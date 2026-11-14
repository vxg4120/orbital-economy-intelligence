# Spec: GCAT's extended catalog (NORAD 100000 and above)

**Status:** active. Found and drafted autonomously on 2026-11-14 during the post-nightly check.
**Owner:** Vib
**Repos touched:** space
**Last updated:** 2026-11-14

## Goal
The US catalog ran out of five-digit numbers in July 2026 and continued at 100000 (Starlink
38128, launched 2026 Jul 14, is NORAD 100001). GCAT followed by splitting its S catalog: part 1
(`satcat.tsv`, S00001 to S69999) is now closed, and part 3 (`satcat100k.tsv`, S100000 onward,
same columns, same S prefix) holds every object catalogued since. GCAT's front page says so:
"Space Force catalog numbers above 100000 are in the file satcat100k". Our loader still pulls
only `satcat.tsv` and `psatcat.tsv`, so every run since 2026-07-21 has landed exactly 69,999
rows and reported `ok`, while the file grew by about 287 KB. Nothing alerted.

The goal is that the GCAT S catalog we land is the whole S catalog, parts 1 and 3, in one run,
and that a gap of this kind can never again sit silent for four months.

## What the data says (production, 2026-11-14, read-only)
- 90 successful `gcat_satcat` runs since 2026-07-21, every one 69,999 rows. Before that the
  count was still rising (69,935 on 2026-07-08, 69,942 on 2026-07-10).
- The graph has 71,308 satellites. All 69,999 with a NORAD below 70000 carry a GCAT id; none
  of the 1,309 with a NORAD from 100000 to 101357 does.
- Those 1,309 have no GCAT owner, status, bus or manufacturer. SATCAT's owner for them is a
  country code, so the Starlinks among them (most of SATCAT's 853 "US" rows in that range) are
  attributed to the operator "United States", not SpaceX.
- Orbit elements are unaffected: 1,030 objects at 100000 and above have current element sets.
- `satcat100k.tsv` on 2026-11-14: 1,354 rows, S100000 to S101353, header identical to part 1,
  no duplicate keys, 1,295 rows with a numeric Satcat and 59 with `NNA`.
  `psatcat100k.tsv`: 1,175 rows, header identical to `psatcat.tsv`.
- Landing part 3 would give 1,308 of the 1,309 a GCAT entry: 1,249 by NORAD and 59 through the
  existing JCAT-number rule for NNA rows (migration 0026). Two stay without one until GCAT
  catches up (101146 and 101357; see Edge cases for 101146).
- 46 rows of part 3 carry a NORAD that SATCAT does not publish (USA 726 to USA 771, launches
  2026-251 and 2026-255). The matcher creates a satellite for a GCAT NORAD it has not seen,
  exactly as it already does for part 1, so the graph grows by 46.

## Architecture decisions
- 2026-11-14: **Parts 1 and 3 land together in one ingest run, under the existing endpoint
  names.** Every reader takes the newest OK `raw_gcat_satcat` run (match, claims, churn, bus,
  stories, retention), and churn compares the newest two. Rejected: a separate
  `gcat_satcat100k` endpoint, because its run would be the newest and every reader would see
  1,354 rows instead of 71,353. Keeping the endpoint name keeps the freshness ledger and the
  retention policy unchanged.
- 2026-11-14: **A run lands both parts or neither.** If part 3 fails after its retries, the
  whole run closes `error` and the readers keep the previous complete run. Rejected: landing
  part 1 alone, because claims closes the GCAT claims of every satellite missing from the
  newest run, so a partial run would retract 1,308 satellites' claims and reopen them the next
  night.
- 2026-11-14: **`runlog.polite_get_parts` fetches several URLs under one run row**, and
  `polite_get` becomes its one-URL case, so the freshness check, the retries and the error row
  have one implementation.
- 2026-11-14: **The payload catalog gets the same treatment** (`psatcat.tsv` plus
  `psatcat100k.tsv`). Nothing downstream reads `raw_gcat_psatcat` today, but a landed snapshot
  that silently omits part of its source is the defect being fixed.
- 2026-11-14: **The analyst catalogs stay out of scope.** `satcat070k`, `satcat270k` and
  `satcat700M` are USSF analyst objects (GCAT: "Temporary catalogs"). CelesTrak's SATCAT does
  not carry them, and before late 2025 GCAT kept them in `csocat`, which we never pulled.
- 2026-11-14: **The build summary reports GCAT coverage, and a gap above 1% of the graph is an
  alert.** The line is `gcat coverage:` with the share of satellites that carry a current GCAT
  id and the newest NORAD numbers without one. When more than 1% lack one it starts with `!!`,
  which the nightly forwards to ALERT_URL. Normal lag is far below that: GCAT updates weekly
  and a large rideshare adds about 120 objects, while 1% is about 713. This gap would have
  alerted in September.

## Constraints
- Raw landing stays verbatim. The loader never edits a value GCAT publishes, including the
  Hawk 16A typo below.
- No change to the matcher, the resolver, churn or bus attribution rules. The anchor rule
  approved on 2026-10-06 (docs/specs/gcat-sibling-links.md) is unchanged.
- No manual pull or rebuild on production. The nightly applies the fix; GCAT's 20-hour
  freshness window means the first run to pull part 3 is the 19:10 UTC run after deploy.
- No box work 07:05 to 07:30 or 19:05 to 19:30 UTC.

## Interfaces & dependencies
- `ingest/runlog.py`: new `polite_get_parts(conn, source, endpoint, urls, min_interval,
  **requests_kwargs) -> list[Response] | None`. Each response carries the shared
  `.oei_run_id`, its own `.oei_bytes` and `.oei_retries`. A failure names the URL in the run's
  notes when there is more than one.
- `ingest/gcat_loader.py`: `SATCAT_URLS` and `PSATCAT_URLS`; `run()` returns the same
  `{"satcat": n, "psatcat": n}`; one raw file is saved per part
  (`satcat-DATE.tsv`, `satcat100k-DATE.tsv`).
- `scripts/build_graph.py`: summary key `gcat_coverage` and the `gcat coverage:` line.
- `api/routers/stories.py`: the notable-reentries lookup builds a JCAT for any NORAD.

## Edge cases
- **A key in both parts** would violate the raw table's primary key. The loader checks for it
  first and fails the run with a message naming the keys.
- **Part 3 is short or empty** (a GCAT outage that still answers 200): it lands as published.
  The coverage line catches a large drop.
- **GCAT's Hawk 16A typo.** Part 3 row S101146 is Hawk 16A (piece 2026-239C) but carries
  Satcat 100146, which SATCAT and GCAT's own S100146 (NNA, piece 2026-170D) give to a Chinese
  object. It is the only row in either part whose JCAT number differs from its Satcat (part 1:
  0 of 69,999). Under the approved anchor rule the row's Satcat wins, so satellite 100146 will
  carry Hawk 16A's GCAT entry beside its own, bus attribution may give it Hawk 16A's bus, and
  the real Hawk 16A (101146) gets no GCAT entry. One object; recorded as an open question
  rather than handled by a new rule.
- **JCAT formatting.** GCAT renders S00001 to S99999 with five digits and S100000 onward
  without padding. Postgres `lpad(text, 5, '0')` truncates longer strings, so
  `'S' || lpad('100001', 5, '0')` is `S10000`. Any JCAT built from a NORAD must use the
  unpadded number at 100000 and above.

## Acceptance criteria
- [ ] A GCAT pull fetches `satcat.tsv` and `satcat100k.tsv` into one `gcat_satcat` run whose
  `rows_ingested` is the sum of both parts and whose `bytes_downloaded` is the sum of both
  downloads.
  `pytest tests/test_ingest_gcat.py -k parts` passes.
- [ ] The payload catalog does the same with `psatcat.tsv` and `psatcat100k.tsv`.
  `pytest tests/test_ingest_gcat.py -k payload` passes.
- [ ] A part-3 failure after its retries leaves no rows from that run and closes the run
  `error`, naming the failed URL.
  `pytest tests/test_ingest_gcat.py -k fails` passes.
- [ ] A key present in both parts fails the run before any row lands.
  `pytest tests/test_ingest_gcat.py -k duplicate` passes.
- [ ] `polite_get` behaves exactly as before.
  `pytest tests/test_ingest_runlog.py` passes unchanged.
- [ ] The summary prints `gcat coverage:` and starts it with `!!` above 1% without.
  `pytest tests/test_build_graph_summary.py` passes.
- [ ] The notable-reentries lookup finds the GCAT row for an ISS-deployed object with a
  six-digit NORAD.
  `pytest tests/test_api_stories.py -k jcat` passes.
- [ ] Full suite, ruff and the dash check are clean.
- [ ] On production after the first 19:10 UTC run that pulls part 3: `gcat_satcat` lands about
  71,350 rows; the summary reads `gcat coverage:` above 99.9%; satellites with a NORAD of
  100000 or more and no GCAT id number 2 or fewer (the GCAT lag plus 101146); every step exits
  0; the slug gate and publish assertions pass.

## Open questions
- **Hawk 16A (decision for Vib).** Should a GCAT S row whose Satcat disagrees with its own
  number be anchored by its number when its COSPAR piece agrees with SATCAT for that number?
  That changes the approved anchor rule in three places that must agree (the matcher's NORAD
  pass, `bus.py` rule 1, `churn.expire_moved_gcat_keys`). Telling GCAT's maintainer about the
  typo is an outward action, also his call.
- Published numbers move when part 3 lands: SpaceX's fleet and the bus and manufacturer
  cohorts gain the objects catalogued since July, "United States" loses most of the 853, and
  the graph gains 46 satellites. The landing snapshot is re-baked after that run.

## Decision log & lessons learned
- 2026-11-14: The defect was found by a number that never moved. A source count that is flat
  for months while the download grows is a truncation or a split, never a quiet source.
- 2026-11-14: The loader was correct for the file it was given. The failure was upstream
  layout, and the only signal was downstream coverage, so the guard belongs downstream.
