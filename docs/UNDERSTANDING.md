# UNDERSTANDING.md — the owner's mental model

*This is not the spec and not the README. This is me explaining the system to myself, the way
I would explain it out loud in an interview, so that every layer is mine and not the AI's. I
built it fast with agents; this document is where I actually internalize it. Plain language
first, jargon second, every term defined the first time it appears.*

---

## 1. The one-paragraph mental model

At CannMenus I solved one problem: dispensaries publish menus, but there is **no universal
SKU** — no shared product ID — so "Blue Dream 3.5g by Brand X" appears a hundred different ways
across a hundred menus, and nobody can tell you it's the same product. My whole company was the
**normalization layer** that said *these hundred strings are one canonical product*, plus the
**brand/MSO hierarchy** (an MSO — Multi-State Operator — is a parent company that owns many retail
brands), plus **polite scraping** that respected each source. The analytics rode on top; the
join was the asset. This project is that exact architecture repointed at Earth orbit. One
physical satellite is a NORAD number in one catalog, an international designator (COSPAR) in
another, a commercial name like "STARLINK-30042", a scholarly ID in a third catalog, and a stale
owner code that still says "OneWeb" three years after Eutelsat bought them. There is **no
universal satellite ID** either. So I built the crosswalk — *which physical object is this, who
owns it right now, what state is it in* — with temporal ownership and per-fact provenance, and I
ran market analytics on top. Satellites are the SKUs; operators are the MSOs; CelesTrak's 2-hour
rule is the scrape politeness; my Data Quality report is the CannMenus 78%→96% accuracy story
retold. **The identity graph is the product; the metrics are the demo.**

---

## 2. The raw layer — what each source actually is

Everything lands first into per-source **landing tables** (`raw_*`), untouched, each row stamped
with an `ingest_run_id` (which pull produced it) and `loaded_at`. Nothing is interpreted here.
This is the "keep the raw scrape verbatim" discipline — you can always re-derive, never lose the
original claim.

### SATCAT — the SKU master (`raw_satcat`)
CelesTrak's Satellite Catalog. This is the object-of-record: every tracked object gets a **NORAD
catalog number** (a 5-digit-and-growing integer assigned by US Space Command) and an **OBJECT_ID**
(the COSPAR/international designator, e.g. `2023-054A` = launch year, launch number, piece letter).
Columns I care about: `norad_cat_id`, `object_id`, `object_name`, `object_type`
(PAY/R/B/DEB/UNK), `ops_status_code`, `owner`, `launch_date`, `decay_date`, apogee/perigee/period.
**Quirks that are features for me:** the `owner` field is a coarse **country/agency code**, not a
company — Starlink is coded `US`, the whole ex-OneWeb fleet is coded `UK`. The names are
uplink/registration style, not commercial. Owner codes go stale the instant an M&A closes. Without
SATCAT: no authoritative object list, no clean NORAD/COSPAR spine to hang everything else on.

### GCAT — the second opinion (`raw_gcat_satcat`, `raw_gcat_psatcat`, `raw_gcat_orgs`)
Jonathan McDowell's General Catalog of Artificial Space Objects (planet4589.org, CC-BY 4.0). A
scholarly counter-catalog with its **own** object IDs (**JCAT**, e.g. `S00001`), its own
`status`/phase taxonomy, richer ownership and program attribution than SATCAT, plus pre-catalog
and analyst objects SATCAT doesn't list. Its `status` is a **physical phase** ("O" = in orbit,
"R" = reentered), not an operational-health field — a crucial distinction I encode later. Dates
are deliberately vague scholarly forms: `1957 Dec  1 1000?`, `1971?`, `2000s?`. The `orgs.tsv`
file is a directory of ~4,000 organizations with owner **codes** (SPXS, ONEWEB, EUTSA) that I use
to enrich operators. GCAT is my **conflict engine**: SATCAT-vs-GCAT disagreements are the seed
corpus for the DQ report. Without it, I have one opinion and no way to say "sources disagree."

### CelesTrak GP / OMM element sets (`gp_elements`)
GP = "General Perturbations." An **element set** (elset) is the orbit description at a moment in
time. I ingest them as **OMM** (CCSDS Orbit Mean-Elements Message, JSON) — deliberately **never**
legacy TLE (Two-Line Element) text, because TLE is a fixed-width format that **cannot represent a
6-digit-plus catalog number**, and the 5-digit space exhausts at 69999 (live catalog max NORAD is
already **69,862** — days from rollover). Every NORAD column is **BIGINT** for exactly this
reason. The orbital elements, in plain English:
- **epoch** — the timestamp the element set describes. The orbit "as of" this instant.
- **mean_motion** — revolutions per day. How many times it laps the Earth daily. From this I
  derive the **semi-major axis** (half the long axis of the ellipse ≈ average altitude + Earth
  radius) via Kepler's third law: `a = (μ / n²)^(1/3)` where μ is Earth's gravitational parameter
  (398600.4418 km³/s²) and n is mean motion in rad/s. This is a **generated column** in the
  schema — Postgres computes it on insert.
- **eccentricity** — how oval the orbit is. 0 = perfect circle, →1 = very stretched. Combined with
  the semi-major axis it gives **apogee** (highest point) and **perigee** (lowest), both also
  generated columns: `a·(1±e) − Earth_radius`.
- **inclination** — tilt of the orbit plane vs the equator, in degrees. 0 = equatorial, 90 =
  polar, 98ish = sun-synchronous.
- **ra_of_asc_node (RAAN)** — Right Ascension of the Ascending Node: which way the orbit plane is
  rotated around Earth's axis. Distinguishes the shells/planes of a constellation.
- **arg_of_pericenter** — where in the orbit the low point sits, measured within the plane.
- **mean_anomaly** — where the satellite is *along* its orbit at epoch. The clock hand position.
- **bstar** — the drag term. How hard the atmosphere is pulling it down; a decay predictor.

Without the fact layer: I have identities but no physics, so no benchmarks.

### Space-Track `gp_history` + SupGP + UCS
- **Space-Track `gp_history`** — the US government's authenticated archive of historical element
  sets, windowed by `CREATION_DATE`. This is where the **9.67 million** backfilled `gp_elements`
  rows come from (vs only 15,932 in the live CelesTrak daily pull). Without it, every metric is a
  single snapshot with no time series — no station-keeping, no time-to-operational.
- **SupGP** (Supplemental GP) — operator-supplied ephemerides. CelesTrak publishes match flags
  (NO MATCH / cross-tag) between what the operator declares and the catalog — **entity-resolution
  signals handed to me for free**. Landed into `raw_supgp_status`. (Currently 0 rows — best-effort;
  see the war story below.)
- **UCS** (Union of Concerned Scientists database, frozen May 2023) — a labeled seed of ~7,500
  satellites with **commercial operator names, users, and purposes**. I treat it as training data
  for name-matching, never as current truth. Without it, my only owner names are country codes.

---

## 3. The identity graph, table by table

Design rules (the interview vocabulary): **surrogate keys everywhere** (a satellite is a
meaningless auto-generated `satellite_id`, not its NORAD number, so identity survives an object
being recataloged); **natural identifiers live in the crosswalk, never as the primary key**;
**every ownership/status fact is time-bounded**; **every value carries its source**; **merges are
logged, never silent.** Live numbers below are from the running DB.

- **`satellite`** — the canonical physical object. `satellite_id` (surrogate PK), `norad_id`
  (BIGINT, UNIQUE, NULL until cataloged), `cospar_id`, `canonical_name`, `object_type`,
  `launch_date`, `decay_date`. The `decay_date` here is the **resolved** value; conflicting claims
  live in assertions. **69,878 rows.** *Failure it prevents:* keying on NORAD directly would break
  the moment two catalogs disagree or an object predates cataloging — the surrogate key means
  identity is never hostage to any one source's numbering.
- **`satellite_identifier`** — the crosswalk, **the heart of the graph**. One row per
  `(satellite_id, id_type, id_value, source)`: id_type ∈ norad | cospar | name_satcat |
  name_gcat | gcat_id | ucs_row | itu_filing. This is literally "these five strings are one
  object." **~417k rows** (139,583 cospar, 69,878 gcat_id, 69,705 norad, etc.). *Prevents:* the
  CannMenus disease — the same object living as N unlinked list-entries with no way to join them.
- **`operator` + `operator_alias` + `operator_relationship`** — the MSO tree. `operator` is
  canonical companies (**1,435** live, seeded by 17 hand-curated + data-driven from GCAT orgs);
  `operator_alias` maps every catalog spelling to one operator (**4,391** aliases); 
  `operator_relationship` is the parentage graph — `child_id`, `parent_id`, `relationship`
  (acquired_by | merged_into | subsidiary_of), with `valid_from` (**131** edges). *Prevents:*
  attributing OneWeb's fleet to a dead brand instead of Eutelsat.
- **`satellite_operator`** — **temporal ownership, SCD Type 2.** A **Slowly Changing Dimension**
  is a dimension (a descriptive attribute like "owner") whose value changes over time. **Type 2**
  means you don't overwrite the old value — you **close the old row and open a new one**, each
  time-bounded, so history stays queryable. Columns: `satellite_id`, `operator_id`, `role`,
  `valid_from`, `valid_to` (NULL = current). **Worked example, verified live:** INTELSAT I (Early
  Bird, `satellite_id` 1317) has *two* rows — `Intelsat [1965-04-06, 2025-07-17)` and
  `SES [2025-07-17, NULL)` — because SES's acquisition of Intelsat closed 2025-07-17. Ask "who
  owned this in 1990?" → Intelsat. "Who owns it now?" → SES. One satellite, two truths, both
  correct, split at the deal-close date. *Prevents:* the single-value overwrite that makes all
  historical analysis silently wrong after any merger.
- **`status_mapping` + `satellite_status_history`** — the canonical status taxonomy. Canonical set
  is exactly seven: ACTIVE | PARTIAL | SPARE | INACTIVE | GRAVEYARD | DECAYED | UNKNOWN.
  `status_mapping` translates each source's raw codes into that set (SATCAT `+` → ACTIVE, GCAT `R`
  → DECAYED), populated **only from source documentation**, never a random blog. Crucially GCAT's
  in-orbit phases map to **UNKNOWN** (GCAT asserts physical presence, not operational health), so
  the resolver falls through to SATCAT's operational code. `satellite_status_history` holds the
  resolved winner over time (**53,426 rows**). *Prevents:* comparing `+` to `O` to `operational`
  as if they were the same alphabet.
- **`source_assertion`** — **per-attribute provenance.** *Every claim every source makes, before
  resolution.* One row per (satellite_id, source, attribute, value, observed_at, ingest_run_id) —
  "SATCAT says owner=US; GCAT says owner=ONEWEB; UCS says owner=OneWeb." **613,421 rows.** The
  `source_key` column (my documented deviation from the spec) keeps an assertion **attachable even
  before it's matched** to a satellite (satellite_id NULL until matched). This is the table that
  makes "**disagreements are data, not errors**" literally true — losers stay queryable forever.
  *Prevents:* the classic ETL sin of resolving-then-discarding, which throws away the exact signal
  (the disagreement) that is my whole flagship report.
- **`merge_log`** — the audit ledger. Every single link and merge writes a row: `surviving_id`,
  `merged_id`, `rule_fired`, `score`, `details` (JSONB). **418,086 rows** (416,760 norad_exact +
  1,326 cospar_exact). *Prevents:* silent merges — the thing that makes a bad entity-resolution
  system un-debuggable. I can replay exactly why any two things became one.
- **`ingest_run`** — the **politeness ledger**. Every pull: source, endpoint, started/finished,
  rows, bytes, `status` (ok | skipped_fresh | error). The whole system queries **only my own DB**
  after ingest, and this table proves the 2-hour rule was honored and the pull happened once.

---

## 4. How matching works

The matcher (`identity/match.py`) runs in two phases, cheap-and-certain first.

**Deterministic passes (authoritative, confidence 1.0):**
1. **NORAD exact** — same NORAD number = same object, full stop. Set-based: COPY the snapshot into
   a temp table, upsert satellites, link norad/cospar/name in a handful of SQL statements.
2. **COSPAR exact** — for NORAD-less rows, match on the normalized international designator. But a
   COSPAR can map to **more than one satellite** (48 such cases live — multiple pieces from one
   launch), so `_find_by_cospar` picks the lowest satellite_id **deterministically** and returns an
   `ambiguous` flag that gets **counted as a DQ signal**, not silently guessed.

**Probabilistic pass (for name-only sources — UCS, GCAT analyst rows):** when there's no shared
ID, I score name similarity and gate it hard. `_score` computes:
`0.60·name_similarity + 0.25·launch_proximity + 0.15·regime_agreement`, where name_similarity is
`difflib.SequenceMatcher` on **normalized** names (`norm_name`: casefold, strip bracketed
suffixes, split glued alpha/digit runs so `STARLINK-30042` = `Starlink 30042` = `STARLINK30042`).
Before scoring, **hard consistency gates** can reject outright: the **orbital-regime gate** (a GEO
comsat at 35,786 km **cannot** be a 550 km LEO cubesat — different regime, impossible match), the
**launch-window gate** (launch dates >30 days apart → reject), and the **country gate**. Then:
- score ≥ **0.92** → auto-link with confidence = score, logged to `merge_log`.
- 0.75 ≤ score < 0.92 → **human-review CSV**, not linked. A staff engineer never lets a fuzzy
  guess masquerade as truth.
- < 0.75 → unmatched.

`merge_log` is what makes this **auditable**: every auto-link records the rule (`name_fuzzy>=0.92`)
and the score and the two names in `details`, so any decision can be second-guessed after the fact.
This is the CannMenus dedupe discipline on display.

---

## 5. Resolution — precedence as config

Once every source's claims sit in `source_assertion`, the resolver (`identity/resolve.py`) picks
**one winner per attribute** and writes it to the dimension tables. The precedence is **config,
not code** — `identity/precedence.yml`:

```
owner:       [operator_seed, gcat, ucs, satcat]   # curated > commercial > coarse country code
status:      [gcat, satcat, ucs]                   # GCAT authoritative for decay; falls through for live ops
decay_date:  [spacetrack_decay, satcat, gcat]
object_type: [gcat, satcat]
name:        [ucs, gcat, satcat]                    # commercial names beat uplink/catalog names
```

Higher-precedence slots (`operator_seed`, `spacetrack_decay`) can be **reserved for later phases**
— if they have no assertions yet, the next source simply wins. **Worked example — one satellite
resolving through conflict:** take an ex-OneWeb bird. Its assertions: SATCAT says `owner=UK`
(country code), GCAT says `owner=ONEWEB`, and status: SATCAT `+` (ACTIVE), GCAT `O` (in-orbit →
UNKNOWN). Resolution:
- **owner** — no operator_seed assertion, so GCAT wins: `ONEWEB`. The `_alias_map` resolves
  `ONEWEB` → operator OneWeb. Then SCD2 kicks in: OneWeb `merged_into` Eutelsat on 2023-09-28, so
  `_write_owner` splits ownership — OneWeb `[launch, 2023-09-28)`, Eutelsat `[2023-09-28, NULL)`.
- **status** — GCAT is first in precedence but maps to UNKNOWN, so it **falls through** to SATCAT's
  `+` = ACTIVE. That fall-through is the whole reason GCAT's physical phase doesn't clobber
  SATCAT's operational health.
- The losing claims (SATCAT `owner=UK`) stay in `source_assertion` forever, feeding the DQ report's
  "stale post-M&A owner" section.

---

## 6. The fact layer + metrics

**`gp_elements`** is a TimescaleDB **hypertable** — a Postgres table that TimescaleDB
auto-partitions by time (`epoch`) into chunks, with compression after 30 days
(`compress_segmentby='norad_id'`). ~9.68M rows and trivially compressible. Semi-major axis /
apogee / perigee are **generated columns** (computed on insert from mean_motion + eccentricity).

**`sat_daily`** is a **continuous aggregate** — a materialized view that TimescaleDB keeps
incrementally up to date. Daily per-satellite stats: `sma_avg`, `sma_stddev`, `perigee_min`,
`apogee_max`, `elset_count`, keyed by **`norad_id` only** — because continuous aggregates
**cannot join across tables**. Real-time aggregation is switched on explicitly so just-ingested
rows show immediately.

**The single most important architectural decision:** operator attribution happens **ABOVE** the
physics aggregate, in a plain view `v_sat_operator_daily`, via a **temporal range-join** of
`sat_daily` to `satellite_operator`. This is deliberate and it's the "senior engineer tell": **a
satellite's orbit does not change the day its owner changes.** If I baked owner into the aggregate,
every M&A would corrupt the physics history. Instead the physics is pure and identity churn only
changes *which operator the view attributes an unchanged orbit to*. The range-join is **half-open**
`[valid_from, valid_to)` — critical, because SCD2 writes two adjacent rows that **share** the split
boundary date; a closed `BETWEEN` would match both and **double-count the transition day**.

The four benchmark metrics — each has a physical meaning **and** a commercial meaning:
1. **Station-keeping tightness** (`v_station_keeping_operator`) — 30-day rolling stddev of daily
   semi-major axis for ACTIVE payloads. *Physically:* how tightly the satellite holds its orbit.
   *Commercially:* a proxy for **propulsion health and operational tempo** — a fleet that holds
   ±5 m is disciplined; a wide tail means birds mid orbit-raising. Live: SpaceX p50 ~42 m with a
   multi-km tail; Eutelsat/OneWeb ~5 m; Planet's doves drift ~0.7 km (by design).
2. **Time-to-operational** (`v_time_to_operational`) — days from launch until semi-major axis holds
   within ±15 km of its shell median for 7 consecutive days (a gaps-and-islands SQL pattern).
   *Physically:* the orbit-raising curve settling. *Commercially:* **deployment efficiency** — how
   fast an operator turns a launch into revenue. Live: SpaceX ~49 d, Planet ~20 d.
3. **Deorbit compliance** (`v_deorbit_compliance`) — for DECAYED/INACTIVE payloads, elapsed time
   from last-active to reentry vs the FCC 5-year rule. *Physically:* end-of-life disposal.
   *Commercially:* **regulatory risk and ESG** posture. (Sparse until deeper history lands —
   honestly flagged.)
4. **Congestion exposure** (`v_congestion_exposure`) — operator fleet distribution across
   altitude×inclination bins, weighted by catalog density per bin. *Physically:* how crowded the
   shells they occupy are. *Commercially:* **collision-risk exposure** — a density proxy, not real
   conjunction data (that's restricted), stated plainly.

**The killer chart** (`v_killer_chart`, SPEC §12) is the acceptance proof: the same metric under
temporal SCD2 attribution vs naive SATCAT owner codes. **Verified live on Eutelsat:** temporal
resolution attributes **708 satellites / 260,148 elset-days**; naive SATCAT owner codes attribute
**57 sats / 19,110 elset-days** — because the 651-satellite ex-OneWeb LEO fleet is coded `UK`
(maps to no operator) and only Eutelsat's legacy birds carry `EUTE`. That's **13.6× more** history
attributed once you resolve country codes + M&A into the actual operating company. That single
number *is* the pitch.

---

## 7. Why this architecture wins — and its honest limits

**Defending it as an engineer:**
- **Provenance → trust.** Because every value in `source_assertion` carries its source, I can
  always answer "why does the system say this?" and show the losing claims. Nothing is a black box.
- **Config → adaptability.** Precedence and match thresholds are YAML. Re-ranking sources or
  re-tuning the fuzzy cutoff is a config edit, not a deploy. New source? Add a precedence slot.
- **Audit → debuggability.** 418k `merge_log` rows mean no merge is a mystery. I can reconstruct
  the exact rule and score behind any identity decision — the thing most ER systems can't do.
- **Temporal dimensions → correct history.** SCD2 + half-open range-joins mean historical
  analytics are *right* across mergers. OneWeb's 2024 performance correctly belongs to Eutelsat.
- **Politeness ledger → sustainable access.** `ingest_run` + conditional-pull logic means I never
  get my CelesTrak IP firewalled or my Space-Track account suspended. Access is a renewable
  resource I manage.

**Honest current limits (I say these before I'm asked):**
- **Status history is snapshot, not true time-series.** `satellite_status_history` mostly reflects
  the current pull; I don't yet have status *transitions* over time, which weakens deorbit
  compliance (it needs a real last-active date).
- **Single-machine.** One Postgres/TimescaleDB instance. Fine at 10M rows; not sharded, not HA.
- **Alias search gap.** Operator resolution is exact-alias-map lookup after casefolding; there's no
  fuzzy operator matching, so a novel spelling of a known operator falls to "unmatched owner"
  rather than being resolved. (Backlog is down to a handful of joint-venture combined codes.)

---

## 8. Ten-question interview drill

**Q1. What actually is this project?** A satellite identity graph: an entity-resolution and
master-data layer that answers "which physical object is this, who owns it now, what state is it
in" across public catalogs that disagree. It's the CannMenus SKU-normalization architecture —
crosswalk, hierarchy, provenance, polite ingest — repointed at low Earth orbit. The metrics are a
demo of the graph, not the point.

**Q2. Why surrogate keys instead of just using the NORAD number?** Because NORAD numbers aren't
universal or stable — an object can predate cataloging (NULL NORAD), catalogs disagree, and the
5-digit space is exhausting. A meaningless `satellite_id` means identity is never hostage to one
source's numbering; the natural IDs live in `satellite_identifier` where they belong, many-to-one.

**Q3. Explain SCD Type 2 like I don't know it.** A Slowly Changing Dimension is an attribute that
changes over time, like a satellite's owner. Type 2 means instead of overwriting the old value you
close the old row (`valid_to`) and open a new one (`valid_from`), so history is preserved. My
`satellite_operator` does this: Intelsat I is owned by Intelsat `[1965, 2025-07-17)` then SES
`[2025-07-17, now)`. Two rows, both true, split at the deal-close date.

**Q4. Why is operator attribution above the continuous aggregate, not inside it?** Because a
satellite's orbit doesn't change the day its owner changes. If I put owner in the `sat_daily`
aggregate, every merger would corrupt the physics. Keeping physics keyed by NORAD only, and
range-joining ownership above it in `v_sat_operator_daily`, means identity churn only re-attributes
an unchanged orbit. Continuous aggregates also literally can't join, which forces the clean split.

**Q5. Why half-open ranges `[valid_from, valid_to)`?** Because SCD2 writes two adjacent ownership
rows that share the boundary date. A closed `BETWEEN valid_from AND valid_to` matches *both* on the
transition day and double-counts that satellite-day under both the old and new operator. Half-open
attributes the boundary day to exactly one owner — the incoming one. I proved this live on
Intelsat.

**Q6. Deterministic vs probabilistic matching — where's the line?** Deterministic is a shared
authoritative ID: same NORAD or same COSPAR = same object, confidence 1.0. Probabilistic is for
name-only sources with no shared ID: I score normalized-name similarity plus launch proximity plus
regime agreement, but I gate hard first — a GEO object can't match a LEO one regardless of name. ≥
0.92 auto-links, 0.75–0.92 goes to a human-review queue, below that stays unmatched. Every
auto-link is logged with its rule and score.

**Q7. "Disagreements are data, not errors" — what do you mean?** Most pipelines resolve a value and
throw the alternatives away. I keep every source's claim in `source_assertion` (613k rows). The
disagreements — SATCAT says a bird is ACTIVE while GCAT says it reentered — are the exact signal my
flagship Data Quality report surfaces. The conflict is the product, so I never discard it.

**Q8. How does the owner code go stale, and how do you fix it?** SATCAT's `owner` is a
country/agency code — the whole ex-OneWeb fleet is coded `UK`, Starlink is `US`. It never updates
for M&A. I fix it with the operator seed + GCAT org codes + SCD2 relationships: GCAT's `ONEWEB`
code resolves to operator OneWeb, then the `merged_into Eutelsat` edge splits ownership at
2023-09-28. That's why naive attribution sees 57 Eutelsat sats and my graph sees 708.

**Q9. What's the killer chart and what does it prove?** It's the same metric computed two ways:
temporal identity resolution vs naive SATCAT owner codes. Live, Eutelsat gets 260,148 elset-days of
history under temporal resolution vs 19,110 naive — **13.6×**, because the entire ex-OneWeb LEO
fleet is invisible to country-code attribution. It proves the identity graph isn't decoration: a
real business metric moves an order of magnitude when you resolve identity correctly.

**Q10. Where does this break, and what would you build next?** Status is still snapshot-not-true-
timeseries, so deorbit compliance is sparse — next I'd ingest Space-Track decay messages and
status transitions for real last-active dates. It's single-machine, so at 10× data I'd partition
Space-Track history across nodes. And operator resolution is exact-alias only, so I'd add fuzzy
operator matching with the same review-queue discipline the satellite matcher already has. I say
these limits up front because knowing exactly where your system is thin is the difference between a
demo and an engineered asset.
