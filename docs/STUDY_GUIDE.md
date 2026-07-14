# Orbital Economy Intelligence — Speaking & Study Guide

Your single reference for talking about this project with authority and understanding the domain it
lives in. Read it enough times that the vocabulary becomes yours. Companion docs go deeper on
specific layers: `UNDERSTANDING.md` (system internals + interview drill), `PRODUCT.md` (use cases),
`research/landscape.md` (the competitive map), `analysis/BEHAVIORAL_STATUS.md` (the oracle research).

---

## How to use this guide

Three modes, three audiences:

- **Speak** (Parts I–II): the pitch, the numbers, the positioning. For interviews, cover letters, DMs.
- **Understand** (Parts III–V): the physics, the catalogs, the players. So the pitch is backed by real knowledge, not memorized lines.
- **Defend** (Part VI): the hard questions — including the ones you've asked yourself — with strong answers.
- **Reference** (Part VII): glossary + flashcards for spaced review.

**A 5-day study plan.** Day 1: Parts I–II until you can deliver the 60-second pitch cold. Day 2: Part III (the physics — this is the part that makes you sound like an insider). Day 3: Part IV (the players — memorize the map). Day 4: Part VI (rehearse the hard questions out loud). Day 5: Part V + label 20 gold cases in `/review` (nothing teaches the domain like arbitrating it). Then reread Part I; it'll mean more.

---

# PART I — THE STORY

## The one-paragraph thesis (memorize this)

> The space economy runs on catalogs built to answer a *physics* question — where is this object, will it collide — not a *commercial* one: who owns it right now, when did it actually die, is the operator compliant. Every expensive question in the industry is a question of **identity, ownership-through-time, and lifecycle**, and no public source maintains those. One satellite is a NORAD number in one catalog, a COSPAR designator, a commercial name, an ITU filing, and a stale owner code that went wrong the moment the company was acquired. I built the crosswalk — with temporal ownership, per-attribute provenance, and an auditable merge log — then ran analytics on top. Finance forced the LEI into existence to solve exactly this class of problem for securities. **Space has no LEI. I built a working prototype of one.**

That last line is the whole pitch. Practice landing it.

## Three elevator versions (pick by who's across the table)

**To a data-platform hiring manager** (lead with the engineering):
> "I built an entity-resolution layer over the public satellite catalogs — 69,878 objects reconciled across three sources with slowly-changing-dimension temporal ownership, per-attribute source provenance, and auditable merges. The catalogs disagree about who owns what and when things died; I made the disagreement machine-readable instead of hiding it behind a curated answer. It's the same master-data problem I solved commercially at CannMenus — SKU normalization without universal identifiers — repointed at low Earth orbit."

**To a space company** (lead with domain fluency):
> "Everyone treats orbital data as a sensing problem. I treated it as a master-data problem. I ingest CCSDS OMM element sets — never legacy TLE, because the catalog is about to exhaust 5-digit numbers — under CelesTrak's enforced rate limits, resolve identities across SATCAT and Jonathan McDowell's GCAT, and benchmark operators on station-keeping, deployment speed, and deorbit compliance from 9.7 million historical element sets. Slingshot bought Seradata because a curated satellite database is a real business; I built the open, provenance-tracked version of that asset."

**To anyone (the shortest version):**
> "I built the open version of the satellite database Slingshot paid to acquire — the layer that answers 'whose object is this, who owns it now, and is it actually alive.'"

## The narrative arc (the story behind the story)

When you're asked to *walk through* the project, tell it as three acts:

1. **The problem is a reframe.** "The insight wasn't technical — it was seeing that a physics community had a data-quality problem it wasn't treating as one. Half the value was deciding the catalog conflicts are the *product*, not noise to clean away."
2. **The build proves velocity.** "I orchestrated frontier models to build in days what a team would take a quarter to do — including adversarial review agents that caught a real bug in my own spec and killed one of my own headline stats when it failed verification. That's table stakes now; the judgment about *what* to build is the differentiator."
3. **The moat is what AI can't generate.** "Anyone can prompt up the pipeline. Nobody can prompt up a year of daily observations, a hand-adjudicated accuracy program, or being the first to make the catalog agent-native. So that's where I pointed the work: a behavioral oracle that infers satellite death from physics, and a gold-standard evaluation with measured error rates."

## The evolution (where it's headed — the ambitious frame)

The project has a thesis it grew into: **the independent behavioral auditor of the megaconstellation era.** The industry launches 3,000+ satellites a year; the incumbent model (human analysts hand-recording lifecycle events) was built for ~100/year and doesn't scale. New disposal rules (FCC 5-year rule, ESA Zero Debris) arrived with no measurement infrastructure — nobody systematically verifies who complies. The only accountability product that exists (the Space Sustainability Rating) is *self-submitted*. So the open lane is: physics-based, involuntary, provenance-backed verification of what operators actually do in orbit. Every layer already built is the auditor's toolkit — identity graph = *who is accountable*, temporal ownership = *accountable at time T*, the physics oracle = *what actually happened*, the gold program = *why the audit is credible*.

---

# PART II — THE NUMBERS

## Stats to memorize (with their definitions — this matters, read the next section)

**Scale:**
- **69,878** canonical satellites resolved; max NORAD catalog number **69,862** (the catalog is days from exhausting 5-digit numbers — see the rollover story in Part III).
- **1,435** operators, **4,391** aliases, resolved into a corporate hierarchy (the "MSO tree").
- **9.67 million** historical element sets (`gp_history`) across 8 benchmark operators, 12 months.
- **613,421** source assertions (individual claims), **418,086** audited merge events.

**The flagship finding — the "killer chart":**
- **13.6×.** Eutelsat's fleet under correct temporal-ownership attribution is **708 satellites / 260,000+ element-set-days**; under naive catalog-owner-code attribution it's **57 / 19,000**. Because SATCAT files the 654 ex-OneWeb satellites under the country code `UK`, which maps to no operator. *This one number proves the whole thesis: how you model ownership changes the answer by more than a factor of ten.*

**Data-quality findings (the "nobody agrees" corpus):**
- **99.3%** of objects shared between SATCAT and GCAT disagree on at least one of five tracked attributes. (The *substantive* conflicts are narrower: decay dates ~12%, object type ~2.1%, status ~0.1% — most disagreement is naming/granularity.)
- **231** SpaceX Starshield satellites the US catalog designates only as "USA 350"–"USA 618"; GCAT names each "Starshield NN-NN."
- **670** objects SATCAT types as DEBRIS that GCAT attributes as functional payloads.
- **35** status conflicts (SATCAT says active/inactive, GCAT says decayed) — including US recon satellites listed "active" 17 years after reentry, and Iridium 33, whose GCAT death date is the day of history's first satellite collision.
- **159** stale post-M&A owners: **139** still coded Intelsat (a year after SES closed) + **20** still coded Inmarsat (3+ years after Viasat closed).

**Operator behavior (from the physics):**
- Station-keeping precision (30-day rolling semi-major-axis stddev): **Iridium ~2.6 m** (legacy discipline) → **SpaceX ~42 m** → **Planet ~655 m** (Doves don't station-keep). The ordering is physically sensible, which is the credibility test.
- Time-to-operational (launch to stable shell): **Planet ~20 days, ICEYE ~28, SpaceX ~78, Kuiper ~73** (Kuiper's *faster* than Starlink per-satellite — the bottleneck is rockets, not spacecraft).
- **497–508** SpaceX physics-confirmed reentries in the window (≈1.4/day, controlled disposal to ~158 km median final perigee) vs **23** dead Iridium satellites lingering at ~750 km that moved <7 km in a year (century-scale litter).

**The audit finding that justifies the oracle:**
- **~865–1,393** satellites the catalog labels ACTIVE that are already in physical decay (the range is a definitional artifact — see next section).

## The measurement-discipline lesson (say this and you sound senior)

Two of the numbers above are ranges — "205 or 228 satellites at Kuiper's shell," "865 or 1,393 walking dead" — and that is **not sloppiness, it's the most important thing to understand about the whole project.** The same English concept ("at the operational shell," "decaying") yields different counts under different operationalizations: is "at shell" within ±15 km, and stable for how many days, measured by mean or by stddev? When you quote a number, quote its definition. This is exactly why the conflict layer exists — because "when did it decay" has three answers depending on which institution you ask and what they mean by "decay." **The discipline of pinning a number to its definition is the difference between a dashboard and an audit.** In an interview, volunteering this unprompted signals that you understand data, not just SQL.

## The corrected stats (intellectual honesty is a feature)

Be ready to say: "Two of my early headline numbers were wrong and I corrected them. I initially claimed 'half the catalog disagrees on all five attributes' — verification showed zero objects hit all five; the honest figure is 99.3% disagree on *at least one*, and I reframed rather than keep the sexier stat. And Starshield is 231, not the 235 I first cited." Volunteering a self-correction is disarming and demonstrates the adversarial-verification habit that makes the whole dataset trustworthy.

---

# PART III — THE DOMAIN (the physics and the catalogs)

You don't need to be an astrodynamicist. You need enough to (a) not get caught out and (b) sound like you belong. Here's the floor.

## Orbits, in plain English

A satellite's orbit is an ellipse around Earth. Six numbers ("orbital elements") plus a drag term describe it completely at a moment in time. The catalogs publish these; everything downstream is derived from them.

- **Epoch** — the timestamp the elements are valid for. Orbits change, so an element set is a snapshot.
- **Mean motion** — how many times per day the satellite circles Earth (revs/day). This is the big one: it *directly determines altitude*. High mean motion = low, fast orbit; low = high, slow. The **semi-major axis** (average orbital radius, and thus altitude) is computed from mean motion via Kepler's third law. When you see "sma" in this project, that's it — and a satellite's sma-over-time line is the single most revealing chart in the whole system.
- **Eccentricity** — how elliptical vs circular the orbit is (0 = perfect circle). Combined with sma it gives **perigee** (lowest point) and **apogee** (highest point).
- **Inclination** — the tilt of the orbit relative to the equator, in degrees. 0° = equatorial, 90° = polar, ~98° = sun-synchronous (a special retrograde orbit imaging satellites love). Inclination + altitude define an orbital "shell."
- **RAAN** (right ascension of ascending node) and **argument of perigee** — the orbit's orientation in space. You rarely need these by name; know they exist.
- **Mean anomaly** — where the satellite is *along* the orbit at epoch.
- **BSTAR** — a drag coefficient. Matters because atmospheric drag is what eventually pulls low satellites down.

**Orbital regimes** (know these cold):
- **LEO** (Low Earth Orbit, ~160–2,000 km) — where the megaconstellations live (Starlink ~550 km, Kuiper ~630 km, Iridium ~780 km). Drag matters here; dead satellites eventually reenter.
- **MEO** (Medium, ~2,000–35,786 km) — GPS/GNSS territory.
- **GEO** (Geostationary, 35,786 km) — satellites orbit at Earth's rotation rate, appearing fixed overhead. Telecom/broadcast (Intelsat, SES, Eutelsat's GEO fleet). Too high to decay; dead ones are boosted into a "graveyard" orbit above.
- **HEO** (Highly Elliptical) — low perigee, high apogee; special-purpose.

## The behaviors you can read from the physics (this is the project's edge)

Because sma is derived from published elements, an sma-over-time line *tells you what the satellite is doing* — even when the catalog's status field is stale or wrong:

- **Active station-keeping** — sma held ruler-flat (tiny variance), often with a faint sawtooth as thrusters re-boost against drag. The satellite is alive and being flown.
- **Passive decay** — a smooth downward drift, no counter-thrust. A propulsionless satellite (or a dead one) losing altitude to drag. Planet's Doves live this way by design.
- **Death signature** — a flat plateau that *breaks* into monotonic decay. Station-keeping stopped; the satellite died at a detectable moment. This is the money signal. The catalog often still says ACTIVE.
- **Controlled deorbit** — a commanded, steep descent. SpaceX drives retired Starlinks down deliberately.
- **Orbit-raising** — a steady climb from a low insertion orbit to the operational shell. A fresh launch deploying. (Must not be mistaken for anomaly — it's healthy.)

The "behavioral status oracle" (in research) is the algorithm that classifies these automatically. Its punchline: **status transitions can't be backfilled from catalogs — but they can be backfilled from physics.** "Last detected maneuver" ≈ "last day alive."

## The catalogs — and why they disagree (this is the heart of it)

There is no single authority for "corporate truth about objects in space." Different institutions maintain different catalogs for different missions, and the gaps between them are where the value is.

- **SATCAT** — the US catalog, produced by the **US Space Force's 18th/19th Space Defense Squadron**, published via **Space-Track.org** and mirrored by **CelesTrak**. Its job is *tracking and collision safety*. Each object gets a **NORAD catalog number** (the 5-digit-going-to-9-digit id) and an **international designator / COSPAR ID** (like `1998-067A` = launch year, launch number, piece). It carries an owner code and an operational-status code — but the **owner is a coarse country/org stamp set near launch and essentially never maintained.** That's not a bug to them; updating corporate ownership isn't their mission.
- **GCAT** — the **General Catalog of Artificial Space Objects**, maintained single-handedly by **Jonathan McDowell**, an astrophysicist at the Harvard-Smithsonian Center for Astrophysics. It's the scholarly *second opinion*: its own JCAT identifiers, a richer status/phase taxonomy, better program and ownership attribution, and lifecycle history. Free under CC-BY (cite him). It is meticulous — but it's *one curator's single resolved answer*, and it flattens disagreement rather than exposing it.
- **UCS Satellite Database** — the **Union of Concerned Scientists'** operator/purpose labeling for ~7,500 active satellites. The best public *commercial-operator* naming. **Frozen since May 2023** (the maintainer's last release, no replacement). Used here as labeled seed data, never as current.
- **Space-Track `gp_history`** — the government's historical archive of element sets. The source of this project's 9.67M-row fact layer. Redistribution is restricted, which is why the repo ships derived aggregates, never raw dumps.
- **SupGP** (Supplemental GP) — operator-provided ephemerides on CelesTrak. Its match-anomaly flags (NO MATCH / cross-tag) are literally published entity-resolution signals.

**Why they disagree — the institutional root cause (this is the insight that impresses):**
Each source is authoritative for a *different attribute*. The Space Force knows where things are; McDowell knows their lifecycle; operators know their own fleets; the UN registry (under the Registration Convention) tracks *states*, not companies, with years of lag; the ITU tracks *spectrum filings* under names that match nothing. Nobody's mandate is "keep corporate ownership current." So the conflicts aren't errors — they *localize each institution's blind spot*, and different blind spots cost different people money. **"Disagreements are data, not errors" is the project's motto, and it's literally true.**

## The rollover story (a great small detail to drop)

The legacy **TLE** (Two-Line Element) format — the fixed-width text format satellites have been published in since the 1960s — can only represent a **5-digit** catalog number (max 99,999, and the practical field exhausts at 69,999). The catalog is *there now* (max NORAD in this dataset: 69,862). The fix is the modern **CCSDS OMM** (Orbit Mean-elements Message) format — JSON/XML, no width limit — and using **BIGINT** for every catalog-number column so 9-digit ids are safe. This project was built OMM-only and BIGINT-native from day one, which dates it as post-rollover-aware. Dropping "I built for the 9-digit catalog era before it arrived" signals you actually understand the plumbing.

---

# PART IV — THE PLAYERS (the who's-who map)

Memorize this map. Knowing who does what — and where *you* sit — is what turns "I did a project" into "I understand this industry." (Sourced from `research/landscape.md`, verified 2026-07.)

## The data authorities (your sources)

- **US Space Force / 18–19 SDS** — produces the authoritative catalog; distributes via Space-Track. The spine.
- **CelesTrak / Dr. T.S. Kelso** — a nonprofit; the de-facto public distribution point for element sets and a clean SATCAT. Politeness-enforced (2-hour update cadence, per-IP limits). A *source*, not a competitor — and a good person to be able to say you respectfully consume.
- **Jonathan McDowell / GCAT** — the scholarly counter-catalog. The single most important "second opinion." Cite him by name; he's a beloved figure and your conflict corpus is genuinely useful *to him*.
- **ESA DISCOS** — Europe's institutional reconciled object database (40,000+ objects, physical characteristics). The closest institutional analog to "one clean record" — but government-gated, single-opinion, not open or conflict-exposing.
- **TraCSS (US Office of Space Commerce)** — the *new* civil, open-architecture traffic-coordination repository (mandated by Space Policy Directive-3), publishing under CC0. This is strategically huge for you: newly-open conjunction/catalog data *raises* the value of a clean identity layer on top. An actively-onboarding org where your project is a direct door-opener.
- **UCS, SatNOGS** — the frozen operator DB and the open crowdsourced-RF-tracking network. Complementary sources.

## The commercial field (your competitive map)

- **Slingshot Aerospace (Seradata)** — *the direct strategic analog.* Slingshot acquired Seradata in **2022** (not 2024 — correct anyone who says otherwise). Seradata's SpaceTrak is a human-curated satellite+launch database going back to Sputnik, sold to governments, manufacturers, **and insurers**. It is the closed, proprietary version of exactly your identity/attribute layer. Your line: "I built the open, provenance-tracked, conflict-exposing version of the asset they paid to acquire." **This is your single strongest pitch.**
- **LeoLabs** — a phased-array *radar* network selling LEO tracking and a commercial catalog; now feeds TraCSS/UDL. They *produce* a catalog from sensors you can't replicate — but they don't do ownership/identity as a product. Complementary: they answer "is there an object here," you answer "whose is it and what state."
- **COMSPOC, ExoAnalytic, Kayhan Space** — physics/sensing/conjunction-assessment incumbents (orbital estimation, optical telescopes, collision-avoidance). None does identity/ownership. Different lane.
- **Privateer (Wayfinder)** — *the cautionary tale.* Wozniak-cofounded "space data for everyone"; killed its free debris product by ~2025 and pivoted to terrestrial analytics. The lesson: a free consumer debris toy isn't a business. It reinforces positioning your work as *infrastructure/moat*, not a free web viewer.
- **Vyoma, Aldoria, Digantara, NorthStar** — the 2024–25 SSA-startup cohort raising money for *sensors and defense* (Digantara raised $50M in Dec 2025). The tell: capital is flowing to tracking hardware, leaving the master-data/reconciliation lane conspicuously unfunded.

## The analysts (your adjacent buyers / distribution)

- **BryceTech** — free, citable market briefings; the journalist/policy go-to (they reported SpaceX ≈50% of 2025 launches).
- **Novaspace** (ex-Euroconsult), **Quilty Space**, **Payload Research** — subscription equity/market intelligence built on *proprietary, hand-built* databases. Your open, queryable, provenance-tracked graph is the machine-readable substrate under the charts they assemble by hand. A data-supplier or guest-research relationship, not competition.

## The regulators (who makes the rules you can measure)

- **FCC** — licenses US satellites; owns the **5-year deorbit rule** (post-mission disposal within 5 years of end-of-life for LEO) and milestone requirements (e.g., **Kuiper's 50%-deployed-by-2026-07-30**). Enforcement telemetry doesn't exist — which is the audit opening.
- **ITU** — the UN body allocating spectrum and orbital slots via filings. "Paper satellites" (filings that never fly, made to reserve spectrum) are a decades-old, unsolved problem. The ITU↔catalog crosswalk is ranked whitespace #2.
- **UN OOSA** — maintains the Registration Convention registry (states, not companies; laggy). Source of the "no corporate authority" gap.
- **ESA** — European disposal guidance, the **Zero Debris Charter**, and the fragmentation database.

## The operators (your subjects — the audited)

- **SpaceX / Starlink** — ~71% of all active LEO payloads; the deorbit machine (508 reentries/year). Also **Starshield** (its NRO/defense line, filed as "USA 3xx").
- **Amazon / Kuiper** — the challenger, racing (and per the data, likely to miss) its FCC July-30 milestone.
- **Eutelsat / OneWeb** — the #2 LEO constellation; the star of the killer chart (acquired OneWeb 2023; the catalog still can't attribute the fleet correctly). Frozen — zero launches in the window.
- **Chinese state megaconstellations** — **Guowang** (China SatNet) and **Qianfan** (SpaceSail); together they've quietly surpassed Kuiper's on-orbit fleet.
- **The M&A chains you resolve** — OneWeb→Eutelsat (2023-09-28), Inmarsat→Viasat (2023-05-30), Intelsat→SES (2025-07-17). These are your temporal-ownership test cases.
- **Iridium, Planet, Spire, ICEYE, Capella** — your benchmark operators; each teaches a different behavior (Iridium = legacy discipline + lingering dead fleet; Planet = passive-decay CubeSats; the others = modern smallsat constellations).

## Where you sit

You are **not** a sensing company (you own no radar/telescope and never will) and **not** a conjunction-assessment company (no covariance physics). You are the **master-data / identity / lifecycle layer** — the reconciliation, provenance, and behavioral-audit lane that the entire field, open and commercial, leaves unoccupied because they all treat orbital data as physics. That's the whitespace, and it's real: no open project maintains a provenance-tracked, temporal, conflict-exposing cross-catalog identity graph, and the one company that does it closed (Seradata) got acquired for it.

---

# PART V — THE SYSTEM (what you built, condensed)

Full internals are in `UNDERSTANDING.md`; this is the version you speak from. Five layers, bottom to top:

1. **Raw / ingestion** — polite, ledgered loaders for each source (SATCAT, GCAT, GP/OMM, SupGP, UCS, Space-Track history). Every network pull is gated and logged in an `ingest_run` ledger so the project never hammers a source. *The politeness ledger is a feature you show off, not plumbing you hide.*
2. **Identity graph (the product)** — the dimension layer. Key tables:
   - `satellite` — the canonical physical object (surrogate key; natural ids live elsewhere).
   - `satellite_identifier` — the **crosswalk**: every id (NORAD, COSPAR, names, JCAT) from every source, with confidence. The heart of the graph.
   - `operator` + `operator_alias` + `operator_relationship` — the corporate hierarchy (the "MSO tree"), including M&A edges.
   - `satellite_operator` — **temporal ownership as SCD Type 2**: ownership is time-bounded, so OneWeb's history stays OneWeb's and Eutelsat's starts at the deal-close date. This is what makes the killer chart correct.
   - `source_assertion` — **per-attribute provenance**: what each source claims, before resolution. This is where "disagreements are data" is physically implemented.
   - `merge_log` — every automated link/merge, with the rule and score. **No silent merges, ever** — full auditability.
   - `status_mapping` / `satellite_status_history` — a canonical status taxonomy with documented per-source mappings.
3. **Fact layer** — `gp_elements`, a TimescaleDB hypertable of 9.67M element sets, with derived columns (sma, apogee, perigee) and a continuous aggregate (`sat_daily`) for per-day-per-object stats. Operator attribution lives in a view *above* the physics aggregate, deliberately — so corporate churn (M&A) never invalidates physics.
4. **Metrics & audit** — operator benchmarks (station-keeping, time-to-operational, deorbit compliance, congestion) and the recurring Orbital Behavior Report.
5. **Surface** — the read-only FastAPI + React "terminal" (Overview, Resolver, Conflicts, Operators, Review), the LifeTrack chart, the gold-standard review workbench.

**How resolution works** (the one-sentence version): deterministic matching first (exact NORAD, exact COSPAR — 99.75% of links), then probabilistic for the rest (normalized name similarity + launch-date proximity + orbital-regime consistency + country), with a review queue for borderline cases and precedence-as-config deciding which source wins each attribute.

**The trust program** (what makes any of it sellable): a gold-standard evaluation — 246 stratified hard cases, AI-researched cited dossiers, **human-adjudicated verdicts**, scored error rates. The sentence it buys: *"every number traces to a source assertion, and resolution accuracy is measured, not assumed."*

**What each layer prevents** (the failure-mode framing that reads as senior): provenance → you can always answer "says who?"; SCD2 → historical analytics stay correct through M&A; merge_log → every automated decision is debuggable; the politeness ledger → sustainable access instead of getting IP-banned; config-driven precedence → adapting to a new source is a YAML edit, not a rewrite.

---

# PART VI — THE HARD QUESTIONS (defend the work)

These are the real objections — including the ones you've raised yourself. Rehearse the answers out loud.

**"Couldn't anyone build this with AI in a weekend now?"**
> "The pipeline, yes — and I'd be suspicious of anyone who claimed otherwise. AI commoditized the *build*. It did not commoditize the three things that actually matter: ground truth (AI can generate a matcher instantly but can't tell you if a match is *correct* — that's domain labor), time (a year of daily observations can't be generated retroactively), and trust (being the source people cite). So I pointed my effort exactly there — a measured accuracy program and a physics-derived behavioral oracle. It's the same lesson as my last company: anyone could scrape dispensary menus; the business was the 78-to-96% accuracy program, which was evaluation and curation and time, never code."

**"How do you know your numbers are right?"**
> "I'm precise about what I've verified. The plumbing is tested and the identity joins are 99.75% exact-key matches — near-zero interpretive risk. Most published claims are claims about what the *sources* say, which are self-verifying — anyone can download both catalogs and check. What I have *not* claimed is that every interpretive resolution is correct — the auto-created operators and status mappings were agent-verified against documentation, never against ground truth, and there's no measured error rate yet. That's exactly why I built the gold-standard program: 246 hard cases I adjudicate against cited sources to produce a real, published precision/recall. 'I can tell you my identity graph's error rate and prove it' is a sentence almost nobody in this field can say."

**"Isn't this already solved?"**
> "Parts are, and I'll tell you which — the insured GEO fleet is thoroughly known via Seradata; conjunction contact-exchange is closing via the Space Data Association and TraCSS. What is *not* solved: nothing scales human curation to 3,000 launches a year, disposal-compliance has rules but no measurement infrastructure, and every accountability product is self-reported. The unsolved lane is automated, physics-based, involuntary verification — which is precisely where I aimed."

**"What's genuinely novel, then?"**
> "The method as product. Three things nobody does in the open: provenance-tracked cross-catalog identity with temporal ownership; conflict reporting as a first-class output instead of a hidden curator's answer; and operator benchmarking from public element sets. The one company that does the identity part closed — Seradata — got acquired for it, which is the market confirming the asset class is real."

**"Why you? You don't have space domain knowledge."**
> "Neither did I have cannabis domain knowledge on day one at CannMenus — I acquired it by *operating* the accuracy program, one edge case at a time. I'm acquiring it here the same way, faster: every gold case I adjudicate, every deorbit I study, is a rep. And the transferable skill is exactly the one that matters — I've built a commercial master-data / entity-resolution system before and I know what 'correct' costs. The domain is learnable; the instinct for what makes reconciled data trustworthy is the hard part, and I have it."

**"This is a data-quality analysis of public catalogs — but you're revealing classified satellites?"**
> "No — and I'm careful about the framing. Every identification is Jonathan McDowell's *already-published* GCAT attribution, credited to him; my contribution is the systematic crosswalk. This genre of open analysis of public tracking data has existed for decades. I document that two *public* catalogs disagree about classified objects — that's data-quality analysis, not disclosure. I never speculate about capabilities or missions."

## The technical interview drill (rapid-fire — have crisp answers)

- *Why SCD Type 2 for ownership?* Because "who owned this satellite" has a different answer on different dates, and analytics that ignore that attribute the wrong company's performance. Time-bounded rows with a half-open interval `[valid_from, valid_to)` — half-open specifically so the transition day isn't double-counted (a bug my own review caught).
- *Why is operator attribution a view above the continuous aggregate, not inside it?* So corporate churn never invalidates physics. The aggregate is keyed on catalog number; ownership is range-joined on top. Identity is mutable, physics isn't.
- *Why OMM and BIGINT?* The 5-digit catalog is exhausting; legacy TLE can't represent 6+ digit ids. Building OMM-only and BIGINT-native is post-rollover-correct.
- *How do you keep from getting rate-limited?* A politeness ledger that gates every pull by the source's minimum interval — and it survived real enforcement (a CelesTrak 403, Space-Track rate-limit stubs returned as HTTP 200 that silently emptied windows until I detected them).
- *Your hardest bug?* Space-Track returning rate-limit errors as HTTP-200 success stubs, which my ingest counted as "0 rows, done" and checkpointed — silent data loss I only caught because four operators were mysteriously missing from a join. Fixed with stub detection + loud failure.
- *A time you disagreed with the spec?* The spec's own example SQL used a closed `BETWEEN` for the SCD2 range join, which double-counts a satellite on its ownership-transition day. I proved it live on INTELSAT I and switched to a half-open interval.

---

# PART VII — REFERENCE

## Glossary (plain definitions)

- **SATCAT** — the satellite catalog; the US Space Force object list distributed via Space-Track/CelesTrak.
- **NORAD catalog number** — the unique integer id for a tracked object (the 5-digit-going-to-9-digit number).
- **COSPAR ID / international designator** — id by launch: `YYYY-NNNP` (year, launch number, piece letter), e.g. `1998-067A` = the ISS.
- **GCAT** — Jonathan McDowell's scholarly General Catalog; the "second opinion" source.
- **JCAT** — GCAT's own object identifier (e.g. `S23728`).
- **OMM (Orbit Mean-elements Message)** — the modern CCSDS element-set format (JSON/XML); replaces TLE, no digit limit.
- **TLE (Two-Line Element set)** — the legacy fixed-width element format; capped at 5-digit catalog numbers.
- **Element set / GP data** — the orbital-parameter snapshot for an object at an epoch.
- **Element / Keplerian element** — one of the six numbers describing an orbit (plus the drag term).
- **Semi-major axis (sma)** — average orbital radius; the altitude proxy derived from mean motion. The key time-series signal.
- **Mean motion** — revolutions per day; determines sma.
- **Perigee / apogee** — lowest / highest point of an orbit.
- **Inclination** — orbital tilt vs the equator, in degrees.
- **Epoch** — the timestamp an element set is valid for.
- **BSTAR** — the drag term in an element set.
- **LEO / MEO / GEO / HEO** — Low / Medium / Geostationary / Highly-Elliptical orbit regimes.
- **Station-keeping** — actively thrusting to hold an orbit; visible as ruler-flat sma.
- **Orbit-raising** — climbing from insertion to operational orbit after launch.
- **Deorbit / reentry / decay** — coming down; "decay date" = reentry date.
- **Graveyard orbit** — a disposal orbit above GEO for retired geostationary satellites.
- **Post-mission disposal (PMD)** — deorbiting/graveyarding a satellite at end of life; the FCC 5-year rule governs LEO PMD.
- **Entity resolution** — deciding which records refer to the same real-world thing (the core discipline here).
- **Master data management (MDM)** — maintaining an authoritative, reconciled record of core entities across sources.
- **Provenance / lineage** — tracking where each data value came from.
- **SCD Type 2 (slowly changing dimension)** — modeling attribute history as time-bounded rows instead of overwriting.
- **Crosswalk** — a mapping table linking ids across sources.
- **Canonical value / taxonomy** — the single normalized value/vocabulary the system resolves to.
- **Deterministic vs probabilistic matching** — exact-key linking vs scored fuzzy linking.
- **Provenance precedence** — the config deciding which source wins for each attribute.
- **Continuous aggregate / hypertable** — TimescaleDB's time-series rollup and partitioned table.
- **18/19 SDS** — the US Space Defense Squadrons that produce the catalog.
- **Space-Track** — the US government portal for the catalog + history; redistribution-restricted.
- **CelesTrak** — Kelso's nonprofit distribution site for elements + SATCAT.
- **SupGP** — supplemental, operator-provided element sets on CelesTrak, with match-anomaly flags.
- **UCS database** — the (frozen 2023) operator/purpose labeling.
- **DISCOS** — ESA's gated reconciled object database.
- **TraCSS** — the new US civil open traffic-coordination repository (CC0).
- **SSA / SDA (Space Situational/Domain Awareness)** — the field of knowing what's in orbit and predicting behavior.
- **Conjunction assessment** — predicting close approaches / collision risk.
- **RSO (Resident Space Object)** — any tracked object in orbit.
- **Seradata / SpaceTrak** — the commercial curated satellite DB Slingshot acquired; the direct analog.
- **The LEI (Legal Entity Identifier)** — the global standard forced into existence to identify financial entities; the analogy for "what space lacks."

## Flashcards (self-test — cover the answer)

- *What's the killer chart's number and why?* → 13.6×; naive catalog owner codes file ex-OneWeb under "UK," so temporal attribution captures 708 satellites vs 57.
- *Why don't the catalogs agree?* → No institution's mandate is maintaining corporate ownership; each is authoritative for a different attribute (position vs lifecycle vs spectrum).
- *What's the one-line pitch?* → Space has no LEI; I built a working prototype of one.
- *What can't a copycat replicate?* → Ground truth, observed time, and being the cited source — not the pipeline.
- *What does an sma-over-time line reveal?* → Whether a satellite is station-keeping, decaying, deorbiting, or raising — i.e. its true status, even when the catalog is wrong.
- *Who is the direct commercial analog and what happened?* → Seradata; Slingshot acquired it in 2022 — the market confirming a curated satellite DB is a real asset.
- *What's the rollover?* → The 5-digit catalog is exhausting; TLE can't hold bigger numbers; OMM + BIGINT is the fix.
- *What makes the data trustworthy?* → Per-attribute provenance + auditable merges + a human-adjudicated gold set with measured error rates.
- *What's the ambitious framing?* → The independent behavioral auditor of the megaconstellation era.
- *Name three orbital regimes and a resident of each.* → LEO (Starlink ~550 km), GEO (Intelsat), MEO (GPS).
