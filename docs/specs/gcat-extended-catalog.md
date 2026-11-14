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
- Landing part 3 would give 1,307 of the 1,309 a GCAT entry: 1,249 rows match by NORAD and 59
  NNA rows through the existing JCAT-number rule (migration 0026), and two of those rows
  (S100146 and S101146) land on the same satellite. Two stay without one: 101357, which GCAT
  has not reached yet, and 101146 (see Edge cases).
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
  newest run, so a partial run would retract the claims of the 1,353 satellites part 3 links
  (1,307 existing and 46 it creates) and reopen them the next night.
- 2026-11-14: **`runlog.polite_get_parts` fetches several URLs under one run row**, and
  `polite_get` becomes its one-URL case, so the freshness check, the retries and the error row
  have one implementation.
- 2026-11-14: **The payload catalog gets the same treatment** (`psatcat.tsv` plus
  `psatcat100k.tsv`). Nothing downstream reads `raw_gcat_psatcat` today, but a landed snapshot
  that silently omits part of its source is the defect being fixed.
- 2026-11-14: **The analyst catalogs stay out of scope.** `satcat070k`, `satcat270k` and
  `satcat700M` are USSF analyst objects (GCAT: "Temporary catalogs"). CelesTrak's SATCAT does
  not carry them, and before late 2025 GCAT kept them in `csocat`, which we never pulled.
- 2026-11-14: **Every file of a pull must be a catalog file.** Each must have at least one data
  row and its key column, found by name (JCAT, or Code for orgs.tsv), or the run closes
  `error`. A part that answers 200 with only its header, or with an error page, would otherwise
  land the other parts alone as an ok run (found in review and reproduced end to end: the claims
  pass closed 7,999 claims on the 1,354 part-3 keys).
- 2026-11-14: **The build summary reports GCAT coverage against the newest snapshot, and a gap
  above 1% of the graph is an alert.** A satellite counts as covered when its current GCAT link
  names a key in the newest OK `raw_gcat_satcat` run, the run the claims pass and bus
  attribution read. The line is `gcat coverage:` with that share and the newest NORAD numbers
  without one. When more than 1% lack one it starts with `!!`, which the nightly forwards to
  ALERT_URL. Normal lag is far below that: GCAT updates weekly and a large rideshare adds about
  120 objects, while 1% is about 713. This gap would have alerted in September. Rejected:
  counting current links alone (the first draft). Links are never retired when a key leaves the
  snapshot, so a later run missing part 3 still read 100% while its claims were closed.

## Constraints
- Raw landing stays verbatim. The loader never edits a value GCAT publishes, including the
  Hawk 16A typo below.
- No change to the matcher, the resolver, churn or bus attribution rules. The anchor rule
  approved on 2026-10-06 (docs/specs/gcat-sibling-links.md) is unchanged.
- No manual pull or rebuild on production. The nightly applies the fix; GCAT's 20-hour
  freshness window means the first run to pull part 3 is the 19:10 UTC run after deploy.
- No box work 07:05 to 07:30 or 19:05 to 19:30 UTC.
- Deploying between 19:30 and 07:05 UTC means the 07:10 run builds the graph before GCAT is due
  again, so its coverage line starts with `!!` (98.2%, 1,309 without) and one alert goes out.
  That alert is true and expected; it clears on the 19:10 run that pulls part 3.

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
- **Part 3 is empty or not a catalog file** (a GCAT outage that still answers 200): the run
  closes `error` and the previous complete run stays the newest. **Part 3 is short but real**
  (a truncated file): it lands as published; the coverage line, measured against that snapshot,
  alerts once more than 1% of the graph has lost its GCAT entry, and a smaller truncation goes
  unalerted.
- **GCAT's Hawk 16A typo.** Part 3 row S101146 is Hawk 16A (piece 2026-239C) but carries
  Satcat 100146, which SATCAT and GCAT's own S100146 (NNA, piece 2026-170D) give to a Chinese
  object. It is the only row in either part whose JCAT number differs from its Satcat (part 1:
  0 of 69,999). Under the approved anchor rule the row's Satcat wins, so satellite 100146 will
  carry two GCAT keys: S100146 through the JCAT-number rule and S101146 through the NORAD pass,
  with S101146's name and its COSPAR 2026-239C. The resolver keeps the greater key per source
  (`identity/resolve.py`, `source_key DESC`), so the card for this Chinese object will read
  "Hawk 16A", owned by HawkEye 360, with S101146's status and object type, and HawkEye 360's
  fleet gains it. Bus attribution breaks the tie by the lesser key, so its bus stays S100146's
  (YX2, XUNQ), and the conflicts page and quality report, which also read the lesser key, show
  nothing new (both rows say status O and carry no decay date). The real Hawk 16A (101146)
  gets no GCAT entry. Found in review and reproduced in a rolled-back simulation. One object;
  recorded as an open question rather than handled by a new rule.
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
- [ ] A key present in both parts fails the run before any row lands, and the key column is
  found by name.
  `pytest tests/test_ingest_gcat.py -k "duplicate or by_name"` passes.
- [ ] A part with no data rows or no key column fails the run before any row lands.
  `pytest tests/test_ingest_gcat.py -k "not_a_catalog_part or only_a_header"` passes.
- [ ] `polite_get` behaves exactly as before.
  `pytest tests/test_ingest_runlog.py` passes unchanged.
- [ ] The summary prints `gcat coverage:`, counted against the newest GCAT snapshot, and starts
  it with `!!` above 1% without.
  `pytest tests/test_build_graph_summary.py` passes, and the coverage test fails when the SQL
  loses its DISTINCT, its NULL-NORAD filter, its `valid_to` filter, its snapshot join, its
  `status = 'ok'` filter, or takes the oldest run instead of the newest (six mutants checked).
- [ ] The notable-reentries lookup finds the GCAT row for an ISS-deployed object with a
  six-digit NORAD.
  `pytest tests/test_api_stories.py -k jcat` passes.
- [ ] Full suite, ruff and the dash check are clean.
- [ ] On production after the first 19:10 UTC run that pulls part 3: `gcat_satcat` lands about
  71,350 rows; the summary reads `gcat coverage:` above 99.9%; satellites with a NORAD of
  100000 or more and no GCAT id number 2 or fewer (the GCAT lag plus 101146); every step exits
  0; the slug gate and publish assertions pass. NORAD 100146's card reads "Hawk 16A", owner
  HawkEye 360, as described under Edge cases, until Vib decides the open question.

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
- 2026-11-14 (review, three lenses with a refuter each): a guard must measure the thing that
  fails. The first coverage line counted crosswalk links, which only grow, so it could not see
  the snapshot shrinking; three reviewers found this independently. Measure against the data
  the readers use (the newest snapshot), and test the guard with the failure it exists for.
- 2026-11-14 (review): a test whose assertion is true by construction (with + without equals
  the total) guards nothing. The coverage test now asserts exact deltas from a seeded baseline.
  A fix-round reviewer then found it still passed with the snapshot join loosened, because its
  dropped key was in no run at all; in production the key is still in older retained runs. The
  test now seeds that, and fails on all six mutants of its SQL.
- 2026-11-14 (review): check any new code that reads columns against the loader's rule
  (columns by name, never by position, because GCAT reorders them). The first duplicate check
  took the first column by position.
