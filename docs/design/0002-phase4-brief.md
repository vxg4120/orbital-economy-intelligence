# Phase 4 Implementation Brief: Manufacturer Identity and Joint Builds

## 0. State this brief was written against (verified, not inherited)

Every number below was re-measured against `localhost:5433/oei` after the `satellite_bus` rebuild at `2026-07-28 23:22:41Z` (commit `ad3d567`, methodology v1.2). Numbers quoted in the upstream measurements that predate that rebuild have been corrected here and the corrected value governs.

`satellite_bus` holds 27,843 rows over 1,125 distinct `manufacturer_code`, 1,037 distinct `manufacturer_group_code`, and 1,037 distinct `manufacturer_slug`. Rollup provenance is `gcat_orgs+override` 12,685, `leaf` 12,120, `gcat_orgs` 3,036, `unresolved` 2 (codes `COL` and `UCSC`). The `spx` cohort is three codes, not two: `SPXS` 12,685 plus `SWARMX` 80 plus `SPX` 64, totalling 12,829. Both benchmark views are currently clean (`v_bus_benchmarks_manufacturer` 1,037 rows over 1,037 slugs, `v_bus_benchmarks_bus` 1,616 over 1,616) and `sum(fleet_total)` equals 27,843 exactly. `bus_benchmark_snapshots` holds 1,037 manufacturer plus 1,616 bus rows, all `snapshot_month = 2026-07-01`, all `methodology_version = '1.0'` while the code declares `1.2`. `benchmark_slug_alias` does not exist (`to_regclass` returns NULL). Twenty-nine manufacturer slugs already collide with bus slugs in the shared `/buses/{slug}` namespace.

---

## 1. Go or No Go

### 1a. Unify manufacturer resolution onto the operator graph: **GO, reshaped**

Ship the operator graph as an **alias-merge layer only**. Do not move the rollup. Concretely:

The GCAT `org_class='B'` parent walk continues to produce `manufacturer_group_code` exactly as it does today, including `ROLLUP_OVERRIDES`. The operator graph is then applied as a single, strictly **merge-only** post-step: two or more `manufacturer_group_code` values that resolve to the same `operator_id` collapse into one cohort. Because the merge key is computed from the **group** code and never from the leaf code, this operation can only ever join cohorts and can never split one. I verified empirically that zero group codes map to more than one post-merge slug.

Three facts make this the only defensible shape. First, the operator graph's parent closure is a sparse, undated copy of GCAT's own Parent edges: with the mandatory class guard it agrees with the current walk on 732 of 733 comparable codes, and it ascends on 475 rows where GCAT ascends on 15,721, so switching the walk buys no rollup quality and costs 26 matched codes (892 rows) that would fall back to leaf and mint new slugs. Second, an ungated walk is catastrophic and a gated one is a no-op, so there is nothing in the middle worth having. Third, keying the merge on the group code rather than the leaf structurally neutralises the owner-side contamination risk: `HSES` (194 Hughes-built GEO satellites) is a leaf whose group is `HEC`, so it is never asked whether it belongs to EchoStar, and `/buses/hec` keeps its name and its fleet.

Add two columns to `satellite_bus` in migration 0011: `manufacturer_operator_id` (the operator of the leaf code, informational) and `manufacturer_group_operator_id` (the operator of the group code, which is the merge key). Document in the migration comment and in `identity/bus.py` that `manufacturer_slug` is a presentation and URL key that is explicitly many-operators-to-one-slug: 26 slugs absorb two or more distinct operators under this design, including `spx`, which covers both SpaceX and Swarm Technologies (SpaceX). Do not represent the slug as an entity identifier anywhere in the API or the docs.

**Keep `ROLLUP_OVERRIDES`.** The claim that it is redundant with the operator graph is true about grouping and false about the URL. I simulated deletion under the exact merge-only rule specified below: the operator graph does correctly re-merge SPXS with SPX, but the fleet-max representative then flips from `SPX` (144 satellites) to `SPXS` (12,685), so `/buses/spx` silently becomes `/buses/spxs` and 12,829 satellites move without a single 404. The override costs one dict entry and removes a 12,829-satellite failure mode. It stays in this pass and its deletion is not on the table.

**Do not take `manufacturer_name` from `operator.canonical_name`.** That changes the display name on 674 of 1,037 cohorts and is a downgrade for CAST, SECM, TsSKB, Sitronics and the Chinese and Russian cohorts, where the operator record carries a transliterated legal name rather than a curated English short name. The name is the GCAT display name of the **representative group code**, which yields zero display-name changes across all 1,034 surviving cohorts, including `Planet` for the merged Planet cohort.

### 1b. Credit joint-build co-builders: **NO GO in this pass**

Do not redefine `fleet_total`. Crediting every listed builder inflates `sum(fleet_total)` from 27,843 to 28,738, destroys a property a reader can and will check, silently shifts 163 of the 1,037 frozen July series with no slug moving and therefore no 404 to notice, adds 79 new slugs to a table that has no slug-alias infrastructure yet, and produces a launch story whose largest single number is Kometa, a Soviet early-warning bureau, going from 37 to 197 satellites and climbing from rank 77 to rank 19. The commercially relevant movers (Lockheed Martin plus 93, UTIAS-SFL plus 23, SECM plus 22, Terran Orbital plus 21, Blue Canyon plus 6) are all second-order behind it.

The credibility half of this change has already shipped. Commit `ad3d567` retracted the unsourced "GCAT lists the prime first" claim in `identity/bus.py`, in `docs/BUS_BENCHMARKS_METHODOLOGY.md` and in the `/api/buses/methodology` payload, and relabelled position-one selection as a convention of ours. That was the correct and sufficient response to the finding that GCAT never documents the slash and that eight org pairs appear in both orders in the same snapshot.

The remaining work is reshaped into an **additive participation metric that moves no published number**, specified in full in section 6, and it must land in a **separate pass and a separate release** from 1a. The reason is not caution for its own sake: the published-number diff for the Planet merge must attribute every moved number to exactly one cause, and 163 co-builder series shifts landing in the same diff would make the merge audit unreadable.

---

## 2. The slug policy, as a single rule

> **`manufacturer_slug = slugify(manufacturer_group_code)`, where `manufacturer_group_code` is produced by the existing GCAT class-B parent walk including `ROLLUP_OVERRIDES`, and is then rewritten to the cohort representative whenever two or more group codes resolve to the same `operator_id`. The representative is the group code with the largest current fleet, with ties broken by group code ascending. `manufacturer_name` and `manufacturer_country` are rewritten to the representative's values in the same statement.**

The tiebreak is a published-URL decision and not an implementation detail. Fleet-max keeps `/buses/plan`; alphabetical would hand the 661-satellite cohort to `/buses/cosmog`, which has two satellites today. Write the rule into the methodology doc and pin it with test 8 below so that a later edit to an `ORDER BY` cannot relocate a published URL.

Measured impact of that exact rule:

- **3 slugs move.** `plabs` (106 satellites), `skybox` (2) and `cosmog` (2) retire into `plan`. That is 110 satellites, 0.4 percent of the attributed corpus.
- **1 merged cohort.** `/buses/plan` goes from 551 to 661, an increase of 20.0 percent. This is the only merge.
- **0 splits.** Structurally impossible under a merge-only rule, and confirmed empirically at zero.
- **1,037 cohorts become 1,034.** `sum(fleet_total)` stays exactly 27,843.
- **3 frozen manufacturer snapshot series break** (`plabs`, `cosmog`, `skybox`), out of 1,037.
- **2 frozen bus-kind snapshot rows** carry a now-dead `metrics->>'primary_manufacturer_slug'`: `skysat` and `skystat`, both pointing at `skybox`.
- **`/buses/spx` does not move.** It stays at 12,829 satellites with display name `SpaceX`.
- **Zero display-name changes** on any surviving slug.

The answer is therefore **not** zero moves, so `benchmark_slug_alias` is required, but it is a **three-row, hand-reviewable table** and every row is a clean one-to-one redirect. Create it in migration 0011 as `(old_slug text, kind text, new_slug text, reason text, effective_month date, PRIMARY KEY (old_slug, kind))` and seed exactly three rows: `('plabs','manufacturer','plan',...)`, `('cosmog','manufacturer','plan',...)`, `('skybox','manufacturer','plan',...)`. Wire it into `_find_group` in `api/routers/buses.py` as a 301-style resolution after a direct lookup misses and before the 404, and into the snapshot history endpoint so a frozen series resolves forward.

**The merge is invisible without an explicit marker, so add one.** `/buses/plan` never 404s, and baseline frozen-versus-live drift is already non-zero on 12 cohorts (`spx` 12,835 to 12,829, `secm` 312 to 307, `spireg` 252 to 251, `adig` 26 to 27, `argot` 15 to 14, `satrec` 12 to 13, `uco` 8 to 9, `creo` 4 to 5, `tamu` 4 to 3, `apex` 3 to 5, `raym` 1 to 4, `etraq` 1 to 2), so no threshold check can distinguish the merge from ordinary catalog drift. Bump `METHODOLOGY_VERSION` to `1.3`, add a dated changelog entry in `docs/BUS_BENCHMARKS_METHODOLOGY.md` naming the Planet merge and its 551-to-661 effect, and add a `cohort_redefined_at` marker column on the manufacturer cohort so the history endpoint can draw the break.

### The blocking prerequisite that makes the merge work at all

`v_bus_benchmarks_manufacturer` does **not** group by slug. Its `GROUP BY` is `(manufacturer_slug, manufacturer_name, manufacturer_group_code, manufacturer_country)`. If the merge rewrites only the slug and leaves the display columns at their leaf values, the view emits four rows for `/buses/plan` (551, 106, 2 and 2) instead of one row of 661. `_find_group` does `fetchone()`, so the detail page, the MCP `bus_detail` tool and every provenance receipt would return an arbitrary one of the four; the leaderboard would list the same slug four times; and `snapshot_benchmarks`'s `ON CONFLICT (snapshot_month, kind, slug) DO NOTHING` would freeze whichever row the planner emitted first and discard the other three permanently. This is not hypothetical: it is exactly what production did with `/buses/raym` earlier today, and the frozen July `raym` row is the one-satellite `RAYM?` phantom rather than the real cohort.

Do both halves of the fix. Rewrite `manufacturer_group_code`, `manufacturer_name` and `manufacturer_country` to the representative in the same statement that rewrites the slug (required for correctness), **and** redefine both benchmark views to `GROUP BY manufacturer_slug` and `GROUP BY bus_slug` alone with `min()` over the display columns (defensive, and it retires this class of bug permanently). The Planet family already shares country `US`, so name and group code are the binding pair.

---

## 3. The exact deterministic tiebreak

Alias resolution is a single CTE, used identically wherever a code is resolved to an operator. This is the literal SQL:

```sql
WITH alias1 AS (
    SELECT DISTINCT ON (a.alias) a.alias, a.operator_id
    FROM operator_alias a
    WHERE a.source IN ('gcat_orgs', 'gcat', 'seed')
    ORDER BY a.alias,
             (a.source <> 'seed'),       -- hand-curated seed wins
             (a.source <> 'gcat_orgs'),  -- then GCAT org codes
             a.operator_id               -- then lowest id, for stability
)
```

Two things about this clause are load-bearing.

The `WHERE a.source IN ('gcat_orgs','gcat','seed')` restriction is mandatory and is a correctness fix independent of everything else. GCAT org codes and SATCAT country codes share one namespace, so without it three manufacturer codes resolve into the owner-country namespace: `POL` (Polyot, 95 satellites) becomes Poland, `COL` becomes Colombia and `LTU` becomes Lithuania. The existing ambiguity guard does not fire on these because they are not ambiguous, they are unambiguously wrong.

The `operator_id` term is what makes the resolution deterministic. `operator_alias.alias` is not unique: the primary key is `(operator_id, alias, source)` and 54 aliases map to two or more operators, 49 of them `gcat_orgs` against `gcat_orgs`, where a source-priority rule alone is a no-op. `identity/resolve.py:154` currently resolves these by Postgres heap order (`SELECT alias, operator_id FROM operator_alias` with no `ORDER BY`, then `setdefault`), which a `VACUUM FULL` or a reload would flip. Do not copy that pattern. Note that under the restricted source set, **zero** ambiguous aliases are used as a `manufacturer_group_code` today, so this clause is insurance rather than an active decision, and test 11 pins it at zero so that it stays that way.

---

## 4. The codes with no operator_alias match: **fall back to the GCAT rollup**

At the leaf level, 369 of the 1,125 manufacturer codes (4,139 satellites, 14.9 percent of the corpus) have no alias under the restricted source set. At the group level, which is what actually matters because the merge keys on group codes, 317 of the 1,037 group codes (4,603 satellites) do not resolve, and 319 of the 1,037 frozen slugs ride on one of them.

Fall back to the incumbent GCAT rollup, and do it **for free**: because the rule is merge-only, an unresolved group code simply forms a cohort keyed on itself, which is precisely what happens today, so its slug does not move and its number does not change. There is no fallback code to write beyond a `COALESCE`.

**Do not create operators for them in this pass.** Running `enrich_operators.py`'s manufacturer scan is a trap that looks like a prerequisite. `_distinct_owner_codes` reads only the `owner` column, so extending it to manufacturers means running `_seed_target_gcat`, `_insert_operator` and `_attach_name_variants` over 366 new codes, which creates 11 new ambiguous **name** aliases covering 707 satellites and takes alias ambiguity from 54 to 65. The largest offender is the poster child itself: `ONEWUS` (650 satellites) attaches the name variant `One Web`, which already belongs to operator 2, OneWeb. Those aliases land in `identity/resolve.py::_alias_map`, which casefolds and `setdefault`s over an unordered `SELECT`, so the scan would inject 11 fresh heap-ordered nondeterminisms into the **owner** resolution path, a system this phase is not supposed to touch at all. The fleet at stake justifies doing the scan eventually and does not justify doing it in the same commit as a URL change.

The correct sequence is: ship 1a with the fallback, then re-capture the baseline, then do the enrich scan as its own pass with its own diff, having first fixed `_alias_map` to resolve deterministically.

---

## 5. Dated M&A rollup: **not implementable honestly, do not attempt**

Mechanically it is available, since `launch_date` is present on 100 percent of the 27,843 rows. Semantically it is worthless. Of the 131 rows in `operator_relationship`, 128 are `subsidiary_of` edges sourced from `gcat_orgs` whose `valid_from` is the **child organisation's GCAT founding date**, not an acquisition date: the US Naval Academy is recorded as `subsidiary_of` the US Navy from 1845-01-01, the Naval Research Lab from 1923-01-01, CALT from 1957-11-11. Four edges carry the `0001-01-01` sentinel. Only three edges are genuine curated M&A (OneWeb into Eutelsat, Inmarsat by Viasat, Intelsat by SES) and none of them carries any manufacturer fleet.

There is exactly one correctly dated acquisition edge with real manufacturer fleet, and it is already handled: child 2705 (Swarm Technologies (SpaceX)) `subsidiary_of` parent 1 (SpaceX), `valid_from 2021-08-01`, 80 satellites. The GCAT walk already puts `SWARMX` under `SPX`, so nothing is gained by reading the date.

Beyond the data quality problem there is a definitional one: there is no build date anywhere in the catalog. `raw_gcat_satcat.extra` has 22 keys and none of them is a build or manufacture date, and launch date postdates the build by one to four years for GEO.

**Honest fallback, to be stated verbatim in the methodology:** manufacturer rollup is undated, current-state attribution. A satellite is credited to the corporate group that owns the factory today, not the one that owned it on build day, because no build date exists. Do not read `valid_from` or `valid_to` in `identity/bus.py`. Since this design never traverses `operator_relationship` at all, that is automatic, and test 12 asserts it.

---

## 6. Joint builds: the chosen crediting model (deferred pass, fully specified)

The model is **prime-only headline plus an additive participation metric**, backed by a bridge table.

Create `satellite_manufacturer_credit (satellite_id bigint, manufacturer_slug text, position smallint, arity smallint, uncertain boolean, PRIMARY KEY (satellite_id, manufacturer_slug))`, populated by expanding `manufacturer_codes` with `WITH ORDINALITY` and applying the same rollup and merge that produced `manufacturer_slug`. Measured shape: 27,843 satellites expand to 28,749 positions across 26,939 single-builder, 902 two-builder and 2 three-builder satellites, and 11 compound satellites self-collapse because both codes roll to the same group, giving 895 non-prime credits net.

`fleet_total` continues to mean position 1 only and does not change on any cohort. A new `participated_total` appears **on the detail payload only** and never on the leaderboard, never in the snapshot metrics blob, and never as a sort key. Orgs that never appear in position one anywhere (79 of them, six above the `min_n=5` floor) do **not** get a slug or a page in this model; that is an accepted limitation and must be written down rather than worked around, because minting 79 new slugs into a table whose primary key is a public URL is the change this whole gating exercise exists to prevent.

**Receipts stay equal to the headline** by making the provenance endpoint role-aware rather than by changing what it counts. `provenance_rows()` gains a `role` parameter defaulting to `prime`, which continues to read `v_bus_sat` one row per satellite, so `count(receipts) == fleet_total` exactly and `tests/test_api_buses.py:126` (`body["total"] == top["fleet_total"]`) keeps passing unmodified. `role=participated` joins `satellite_manufacturer_credit` and returns every position, so `count(receipts) == participated_total` exactly. Two headline numbers, two receipt sets, each reconciling to its own.

Three constraints on the implementation. Do **not** turn `v_bus_sat`'s manufacturer dimension into a bridge: `v_bus_benchmarks_bus` reads the same view, so the 904 compound satellites would silently double-count in every bus-model metric, and `test_provenance_receipts_reconcile_with_headline` only tests the top row (SpaceX, not compound) and would not catch it. Do not credit a non-first position whose token carried the `?` uncertainty marker; that costs 20 rows and removes the objection that a GCAT guess is being promoted into a published number. Note that `RAYM?/GSFC` is the one row where `?` sits on the **first** token, so the rule must be evaluated per position and not assumed to apply only to tails.

Finally, do not build this on the operator graph. Only 123 of the 255 distinct non-first-position codes have an alias under the restricted source set, so resolving co-builders through the operator layer would silently drop half of them. Build it on the same GCAT path that produces the prime attribution.

---

## 7. Test plan

Every assertion below is a new or modified test in `tests/test_bus_build.py`, `tests/test_api_buses.py` or a new `tests/test_slug_stability.py`. Expected values are exact and were measured against the current build.

1. **URL stability gate.** Capture the set of 1,037 `manufacturer_slug` values from the pre-change build. After the rebuild, assert that every one of them either exists in `v_bus_benchmarks_manufacturer` or has a row in `benchmark_slug_alias`. Assert the alias table holds exactly three manufacturer rows and that its content is exactly `{('plabs','plan'), ('cosmog','plan'), ('skybox','plan')}`.
2. **Frozen-snapshot resolution gate.** Assert that all 1,037 distinct slugs in `bus_benchmark_snapshots WHERE kind='manufacturer'` resolve to a live cohort directly or through `benchmark_slug_alias`, with **0** unresolvable. Assert exactly 3 of them resolve via the alias table.
3. **Second frozen surface.** Assert that every `metrics->>'primary_manufacturer_slug'` across the 1,616 `kind='bus'` snapshot rows resolves the same way, with **0** unresolvable and exactly **2** rows resolving via the alias table (`skysat` and `skystat`, both `skybox` to `plan`).
4. **SpaceX outcome pin, independent of provenance strings.** Assert `count(*) FROM satellite_bus WHERE manufacturer_slug = 'spx'` equals **12,829**, and that `manufacturer_code = 'SPXS'` yields `manufacturer_group_code = 'SPX'`. This replaces the current `tests/test_bus_build.py:65` assertion `rows == [("SPX", "gcat_orgs+override")]`, which pins a string that legitimately changes and would be edited away during the refactor, leaving 12,685 satellites unguarded.
5. **Override still fires.** Assert `count(*) FROM satellite_bus WHERE rollup_source = 'gcat_orgs+override'` equals **12,685**.
6. **View cardinality invariant, both views.** Assert `count(*) = count(DISTINCT manufacturer_slug)` in `v_bus_benchmarks_manufacturer` and `count(*) = count(DISTINCT bus_slug)` in `v_bus_benchmarks_bus`. Expected row counts: **1,034** and **1,616**.
7. **Planet merge pin.** Assert `fleet_total = 661` for slug `plan`, with `manufacturer_group_code = 'PLAN'` and `manufacturer_name = 'Planet'`, and that slugs `plabs`, `cosmog` and `skybox` return no row from the view.
8. **Representative tiebreak pin.** Assert that for the merged Planet cohort the surviving group code is the largest-fleet incumbent (`PLAN`, 551) and not the alphabetically first (`COSMOG`, 2). This is the test that stops a later `ORDER BY` edit from relocating a published URL.
9. **Merge count and split gate.** Assert that exactly **one** post-change slug contains more than one pre-change slug, and that the set is exactly `{'plan'}`. Assert that **zero** pre-change slugs map to more than one post-change slug.
10. **Alias source restriction.** Assert `manufacturer_group_operator_id IS NULL` for group codes `POL`, `COL` and `LTU`, and assert that slug `pol` still reports `manufacturer_name = 'Polyot'` with **95** satellites. Assert no row in `satellite_bus` resolved its operator through an alias whose source is `satcat` or `satcat_sources`.
11. **Determinism.** Assert that **0** aliases used as a `manufacturer_group_code` map to more than one `operator_id` under the restricted source set, and that running the `alias1` CTE twice in one transaction returns byte-identical results.
12. **No relationship traversal.** Assert that `identity/bus.py` contains no reference to `operator_relationship` (source-level check), and pin the outcomes an ungated walk would break: `gsfc` = **66**, `jpl` = **50**, `sast` = **188**, `resh` = **156**, `cast` = **414**, `nrl` = **100**. An ungated walk costs 31 slugs and 943 satellites and splits `/buses/sast` from 188 to 1, so these pins are the tripwire.
13. **Unresolved fallback is structurally the incumbent.** Assert that every row with `manufacturer_group_operator_id IS NULL` satisfies `manufacturer_slug = slugify(manufacturer_group_code)`, and that total attributed rows remain **27,843**.
14. **Sum identity.** Assert `sum(fleet_total)` over `v_bus_benchmarks_manufacturer` equals `count(*) FROM satellite_bus WHERE manufacturer_slug IS NOT NULL` equals **27,843**.
15. **Cross-kind collision gate.** Assert the count of slugs present in both benchmark views is exactly **29** and that the set is unchanged from the pre-change build. This class is currently invisible to every guard in the repo: `/api/buses/arrow` today serves the 3-satellite manufacturer "Arrow Sci Tech" and shadows the 666-satellite `ARROW` bus model, which is unreachable without `?kind=bus`. A new collision would be a 200 with the wrong entity, which is strictly worse than a 404.
16. **Before-and-after published-number diff with an explicit allowed-change list.** Re-capture a local baseline with `scripts/diff_published_buses.py --capture` against the **same database immediately before** the change (this holds data constant so every difference is attributable to code), apply the change, rebuild, then run `--gate --allow plan`. The allowed change list is exactly: three manufacturer cohorts vanish (`plabs`, `cosmog`, `skybox`), one manufacturer cohort changes `fleet_total` (`plan`, 551 to 661), and two bus cohorts change `primary_manufacturer_slug` (`skysat` and `skystat`, `skybox` to `plan`). Every other cohort must show zero change on every metric. Extend `diff_published_buses.METRICS` to include `primary_manufacturer_slug` and `primary_manufacturer` before running this, because the tool does not currently track them and a manufacturer partition change would silently relabel bus pages while the gate reported no change.
17. **Baseline re-capture, before it is used as a gate.** The committed `tests/baselines/published_buses_baseline.json` was captured from the live site at `2026-07-28T23:17:52Z`, five minutes before the `?`-marker rebuild, by a dict comprehension keyed on slug over 1,038 view rows. It holds 1,037 entries and records `raym` as `fleet_total 1, name "RAYM?"`, silently having dropped the real Raymond EL cohort. Gating Phase 4 against this file would mis-attribute three satellites to Phase 4. Re-capture it from the post-rebuild database and commit the replacement as a distinct, clearly-messaged commit before any Phase 4 diff runs.
18. **Snapshot month is frozen once.** Change `snapshot_benchmarks` to insert only when the current month has no rows at all, rather than relying on per-slug `ON CONFLICT DO NOTHING`, and assert that with July 2026 already populated it inserts **0** rows for both kinds. The current behaviour is not "immutable monthly", it is "any slug that did not exist at first freeze gets appended later": 2,650 rows were written on 2026-07-23 and three more (`bus/argonaut`, `bus/et-01`, `manufacturer/argosp`) were appended on 2026-07-27 into the same frozen month.
19. **Bus leaderboard is untouched.** Assert `v_bus_benchmarks_bus` still has 1,616 rows and that `fleet_total` is unchanged for every `bus_slug`.
20. **Methodology version and changelog agree.** Assert `METHODOLOGY_VERSION == '1.3'` and that the topmost dated entry in `docs/BUS_BENCHMARKS_METHODOLOGY.md` names that version and describes the Planet merge.

Wire `scripts/diff_published_buses.py --gate` into `scripts/daily_ingest.sh` between `build_bus.py` and `quality/report.py`, and make `build_bus.py` refuse to commit when the gate fails. Today the nightly runs `ingest_all`, `build_graph`, `build_bus` and `quality/report.py` with nothing in between, `quality/report.py` contains zero bus or slug checks, and the gate tool that exists for precisely this purpose is never invoked, so the first unattended nightly after an attribution change would freeze the result into the archive before a human sees a diff. Note that `daily_ingest.sh` runs under `set -euo pipefail`, so a build assertion failure will also halt the DQ report; that is the intended behaviour but should be called out in the runbook.

---

## 8. Explicitly out of scope for this pass

**Do not derive the slug from `operator.canonical_name`.** It moves 734 of 1,037 cohorts and 23,699 of 27,843 satellites, kills 715 frozen manufacturer slugs and 929 frozen bus-kind rows, and it **splits 25 cohorts** (the upstream measurement's headline said 22 while its own evidence block enumerated 25; 25 is correct). A slug alias row is a function from one old slug to one new slug, so a split has no legal target: `/buses/spx` would become `spacex` (12,749) plus `swarm-technologies-spacex` (80) and could not be redirected at all. If the operator name is wanted in the URL, do it once, deliberately, as a versioned v2 URL space with 301s and a cohort split-and-merge event log, which is materially more work than this phase scopes.

**Do not traverse `operator_relationship` for the manufacturer rollup.** Ungated it costs 31 published slugs, 943 satellites and 15 merges, rolling NASA field centres into NASA (`gsfc` 66 to 178), Russian design bureaus into Roskosmos (`resh` 156 to 541), and Aerospace Corp into the Department of Defense, and it splits `/buses/sast` from 188 to 1. Gated on `operator_class='commercial'` it produces the identical slug outcome to not walking at all, so there is no version of this worth running. Publish that equivalence as a finding in the DQ report rather than shipping the walk. (For the record, the 31-slug figure traverses all 128 edges including the 38 that carry a non-null `valid_to`; filtering to still-in-force edges gives 24 slugs and 790 satellites. Neither variant is a candidate.)

**Do not delete `ROLLUP_OVERRIDES`.** See section 1a: deletion flips the fleet-max representative from `SPX` to `SPXS` and moves 12,829 satellites off the site's largest page without a single 404.

**Do not run the `enrich_operators` manufacturer scan.** See section 4: it creates 11 new ambiguous name aliases covering 707 satellites and injects nondeterminism into the owner resolution path.

**Do not implement fractional credit.** It is foreclosed, not merely unattractive: `tests/test_api_buses.py:126` asserts `body["total"] == top["fleet_total"]` and `provenance_rows()` returns whole satellite rows, so a fractional headline (Terran Orbital at 29.5) cannot be reconciled with 40 receipts, and every derived metric (`decayed_share_pct`, `gp_coverage_pct`, the `tto_shell_n >= 3` filter, the `min_n=5` floor) is an integer count over rows. Do not spend design time on it.

**Do not co-locate 1a and 6 in one release.** The Planet merge diff must be readable, and 163 co-builder series shifts in the same diff would bury it.

**Do not publish a "Terran Orbital was undercounted by 27 percent" claim.** It does not reproduce under any natural definition. The defensible sentence, with its denominator stated, is that crediting co-builders raises Terran Orbital from 19 to 40 satellites (110 percent), or from 56 to 79 counting its Tyvak subsidiary (41 percent). The 27.3 percent figure is `21/(56+21)`, which double-counts three satellites and omits two. Also note that `docs/design/0001-tenancy.md` around line 916 cites "Terran Orbital's 37 attributed payloads"; 37 is Tyvak's count, not Terran's, and that line needs correcting.

**Do not cite Blue Canyon under Raytheon, Terran Orbital under Lockheed, or Millennium under Boeing as wins delivered by this phase.** None of them is in the operator graph: there is no Blue Canyon operator, no Terran Orbital operator and no Raytheon operator, and Millennium currently rolls up correctly through a GCAT edge that the operator graph would lose. Those three require new curated entries in `identity/operator_seed.yml`, and once curated they will show up under the existing GCAT walk anyway.

**One pre-existing item to note but not fix here:** the frozen July 2026 row for `/buses/raym` records `display_name 'RAYM?'` with `fleet_total 1`, while the live cohort is `Raymond EL` with 4. That corruption predates Phase 4 and was caused by the same view-grouping defect described in section 2, now guarded by `test_manufacturer_slug_is_unique` and by test 6 above. Decide separately whether to annotate or restate that single archive row; do not fold it into the Phase 4 diff, and attribute the delta to the `?`-marker fix in commit `7ea6e1b` in the methodology changelog so it is not later misread as Phase 4 collateral.