# Competitive & Open-Source Landscape

**Subject:** Orbital Economy Intelligence — a satellite identity graph + catalog-analytics platform.
**What it is (the thing being positioned):** a 69,878-object cross-catalog identity graph over SATCAT / GCAT / UCS with SCD2 temporal ownership, per-attribute provenance (`source_assertion`), an auditable `merge_log`, a ~9.67M-element OMM fact layer (`gp_elements`), operator benchmark metrics, and a conflict / data-quality report. See `docs/SPEC.md` §1–2.
**Research date:** 2026-07-10. Every claim below carries a URL and a date; anything unverifiable is tagged `[unverified]`.

The one-line finding: **the entire incumbent field — commercial and open — treats orbital data as a physics/sensing problem (where is the object, will it collide). Almost nobody treats it as a master-data / entity-resolution problem with first-class provenance and conflict reporting. That is this project's whitespace, and it is real.**

---

## 1. Open-source / open-data neighbors

### 1.1 GCAT — Jonathan McDowell's General Catalog of Artificial Space Objects
- **What:** the scholarly counter-catalog. ~5-or-9-digit JCAT identifiers, its own status/phase taxonomy, ownership/program attribution, and pre-catalog + analyst objects. Release 1.8.0 dated 2025-11-10; data update 2026-03-09. Maintained by an astrophysicist at the Center for Astrophysics | Harvard & Smithsonian. https://planet4589.org/space/gcat/ (accessed 2026-07-10)
- **License:** CC-BY — free to use with citation `data from GCAT (J. McDowell, planet4589.org/space/gcat)`. https://planet4589.org/space/gcat/ (2026-07-10)
- **Overlap / difference:** GCAT is the single best *second opinion* source and this project ingests it (`raw_gcat_satcat`, `raw_gcat_psatcat`, `raw_gcat_orgs`). GCAT's JCAT `Satcat` field is a 1:1 crosswalk to US SATCAT numbers (S46112 ↔ SATCAT 46112). https://www.planet4589.com/space/gcat/web/cat/cats.html (2026-07-10). **Crucial difference:** GCAT is *one curator's resolved answer*. It does not expose, per attribute, "SATCAT says X, GCAT says Y, here is the confidence and the merge decision." This project's conflict layer is exactly the machine-readable disagreement between GCAT and SATCAT that GCAT itself flattens.

### 1.2 CelesTrak (Dr. T.S. Kelso, a 501(c)(3))
- **What:** the de-facto public distribution point for GP/OMM element sets and a curated SATCAT (`satcat.csv`, 9-digit-capable). The upstream this project ingests from. https://celestrak.org/satcat/ (2026-07-10)
- **License / posture:** attribution required; no bulk re-hosting; politeness enforced (GP updates every 2h, one-download-per-update per IP, ~100 MB/day firewall). See `docs/SPEC.md` §2.
- **Overlap / difference:** CelesTrak is a *source*, not a competitor. It distributes clean elements and a catalog; it does not do cross-catalog operator normalization, temporal ownership, or M&A lineage. SupGP match anomalies (NO MATCH / cross-tag) are entity-resolution signals this project consumes (`raw_supgp_status`).

### 1.3 Space-Track.org (US Space Force / 18th SDS)
- **What:** the authoritative government catalog + historical GP archive (`gp`, `gp_history`, `satcat`, `decay`). https://www.space-track.org/documentation (2026-07-10)
- **License:** free account; **redistribution restricted** by user agreement — this project ships code/schema/derived aggregates only, never raw dumps.
- **Overlap / difference:** it is the spine of the fact layer's history, but its owner codes are coarse (country/org) and go stale on M&A — precisely the weakness this project's operator graph corrects.

### 1.4 ESA DISCOS (Database and Information System Characterising Objects in Space)
- **What:** ESA/ESOC single-source reference — launch info, registration, launch-vehicle and spacecraft characteristics (size, mass, shape, owner), orbital histories for 40,000+ trackable unclassified objects. https://discosweb.esoc.esa.int/ (2026-07-10)
- **Access:** **gated** — need-to-know, restricted to research institutes / government / industry of ESA member states, with quotas. https://discosweb.esoc.esa.int/ (2026-07-10)
- **Overlap / difference:** DISCOS is the closest institutional analog to "one reconciled object record with physical attributes." But it is European-government-gated, not open, and (per its literature) a curated single record — not a provenance-tracked, conflict-exposing graph. This project is the open, auditable version of the same reconciliation instinct.

### 1.5 UCS Satellite Database (Union of Concerned Scientists)
- **What:** operator/user/purpose attributes for ~7,560 active satellites; the best public commercial-operator labeling. **Frozen** at 2023-05-01 after 45 updates over 18 years (curator Teri Grimwood's last release). https://www.ucs.org/resources/satellite-database (2026-07-10)
- **License:** free. No official replacement announced. https://www.ucs.org/resources/satellite-database (2026-07-10)
- **Overlap / difference:** used here as *labeled seed / training data* for operator name-matching (`raw_ucs`), never as current. Its abandonment is a gap this project's live, provenance-tracked operator layer partially fills.

### 1.6 SatNOGS / Libre Space Foundation
- **What:** open-source global network of crowdsourced satellite ground stations; SatNOGS DB aims to be an open, machine-readable database of all artificial objects, focused on transmitter/frequency metadata. https://www.libre.space/projects/satnogs/ and https://satnogs.org/ (2026-07-10)
- **License:** free/libre (AGPL-family stack).
- **Overlap / difference:** complementary, not competing. SatNOGS DB is strong on RF/telemetry identity (what frequency, what transmitter) and weak on ownership/M&A/status reconciliation. A future crosswalk into SatNOGS transmitter IDs would be additive.

### 1.7 KeepTrack.space
- **What:** AGPL-licensed TypeScript astrodynamics + 3D visualization app ("for non-engineers"); simulates 2.5M debris at 60fps. Runs in ops centers and classrooms. https://github.com/thkruz/keeptrack.space (2026-07-10)
- **Overlap / difference:** it is a *visualization/propagation* front end over the same public elements. Zero overlap with the identity/provenance layer — in fact a natural consumer of it. (KeepTrack also publishes readable "deep dive" writeups, e.g. on LeoLabs. https://keeptrack.space/deep-dive/leolabs — 2026-07-10.)

### 1.8 Propagation/astrodynamics tooling: Skyfield, poliastro, satellite-js, python-sgp4
- **What:** the standard libraries that turn OMM/TLE into positions via SGP4/SDP4. Skyfield and poliastro both ingest OMM; python-sgp4 (Brandon Rhodes) is the reference propagator; satellite.js is the JS equivalent. https://rhodesmill.org/skyfield/api-satellites.html , https://github.com/brandon-rhodes/python-sgp4 (2026-07-10)
- **Overlap / difference:** pure math libraries, keyed on NORAD ID. They answer "where is 46112," never "which physical object / owner is 46112." This project is the identity layer these tools implicitly assume and never build.

### 1.9 Catalog-reconciliation / ownership / entity-resolution projects (searched hard)
- **libSATCAT** (wojciech-graj): a SATCAT *parser*, not a reconciler. https://github.com/wojciech-graj/libSATCAT (2026-07-10)
- **DataHub GCAT mirror**, **Kaggle UCS mirrors**: re-hosts of frozen data, no reconciliation. https://datahub.io/technology/gcat-artificial-space-objects (2026-07-10)
- **Academic practice:** papers cross-match NORAD IDs against GCAT bus/classification fields ad hoc (e.g., FCC-filing-derived bus classification cross-checked against launch manifests), but as one-off methods sections, not as a reusable, provenance-tracked graph. https://www.planet4589.com/space/gcat/web/cat/cats.html (2026-07-10)
- **Verdict:** I found **no open project that maintains a provenance-tracked, temporally-versioned, conflict-reporting cross-catalog identity graph.** Everyone either curates one opinion (GCAT, DISCOS), distributes elements (CelesTrak, Space-Track), parses one source (libSATCAT), or propagates orbits (Skyfield et al.). `[This negative is the core positioning claim; it is a "could not find," which is weaker than a "verified does-not-exist" — treat as strong-but-not-absolute.]`

---

## 2. Commercial players

### 2.1 Slingshot Aerospace — the direct strategic analog
- **What they sell:** SSA / space-traffic-coordination platform, plus **Seradata's SpaceTrak** — a curated satellite + launch database covering every launch since Sputnik 1957, relied on by governments, agencies, manufacturers, launch providers, operators, **and insurers**. https://www.slingshot.space/product-seradata (2026-07-10)
- **Why they bought Seradata:** acquired (with Numerica's SDA division) on **2022-08-03** — *not 2024*. The thesis: a curated satellite/launch database is a durable business asset that anchors an SSA platform. https://spacenews.com/slingshot-aerospace-acquires-numericas-space-division-and-seradata/ (2022-08-03; accessed 2026-07-10)
- **Funding:** ~$120.2M total; a grant round 2025-01-20 and an unattributed VC round 2025-10-28; valuation not public. https://www.cbinsights.com/company/slingshot-aerospace/financials (2026-07-10). "Global Data Marketplace" and layoffs — `[unverified: no public source found]`.
- **Overlap:** **highest of anyone.** Seradata SpaceTrak *is* a commercial, human-curated version of this project's identity/attribute layer. The difference: Seradata is proprietary, subscription-gated, and (as a curated DB) does not expose machine-readable per-attribute provenance or an open conflict log. This project is explicitly "the open, engineering-grade version of that asset" (`docs/SPEC.md` §1).

### 2.2 LeoLabs — radar sensing + commercial catalog
- **What:** global phased-array radar network; sells LEO tracking, mapping, and a commercial object catalog. In **2025** landed >$60M in awards (US gov bookings +186% YoY), a $60M SpaceWERX STRATFI (2025-03), the Scout mobile radar class (2025-04), and a first-of-kind joint Commerce/Space Force license feeding its full LEO catalog into the Unified Data Library and TraCSS (2025-09). https://leolabs.space/press/leolabs-announces-next-generation-expeditionary-radar-for-advanced-space-domain-awareness-missions/ , https://spacenews.com (2025; accessed 2026-07-10)
- **Overlap:** minimal on identity; they *produce* a catalog from sensors this project cannot replicate. Complementary — LeoLabs answers "is there an object here," this project answers "whose is it and what state."

### 2.3 COMSPOC — physics-based SSA software
- **What:** COTS SSA software (SSASuite, ODSSA, SOTA, SEG, "Mission Awareness") for real-time orbital estimation, threat detection, interference prediction, maneuver planning. https://www.comspoc.com/ (2026-07-10)
- **Overlap:** none on identity/ownership. Pure orbital-dynamics estimation.

### 2.4 ExoAnalytic Solutions — optical sensing at scale
- **What:** the world's largest commercial optical telescope network (350+ autonomous telescopes), strong in GEO/MEO. https://exoanalytic.com/space-intelligence/ (2026-07-10)
- **Overlap:** none on identity; a sensing incumbent.

### 2.5 Kayhan Space — conjunction assessment / traffic coordination
- **What:** Pathfinder (3.0) — conjunction risk, maneuver planning, autonomous machine-to-machine traffic coordination; Pathfinder Classroom for universities. ~$10.7M raised (last public: $7M, 2023-09). https://payloadspace.com/kayhan-space-closes-7m-seed-extension-updates-pathfinder/ (2023-09; accessed 2026-07-10). No 2025 raise found — `[unverified]`.
- **Overlap:** none on identity; a CA/STM incumbent.

### 2.6 Privateer (Wayfinder / Crow's Nest) — the cautionary tale
- **What:** Wozniak-cofounded "one-stop shop for space data." Wayfinder (2022) + free Crow's Nest collision tool; bought Orbital Insight (2024-04). But by ~2025-09, space is a *small* focus, terrestrial data analytics dominates, and the Wayfinder debris solution is marked **Cancelled**. https://www.factoriesinspace.com/privateer (accessed 2026-07-10)
- **Overlap:** *was* the closest "open data aggregator" pitch; its pivot away is a signal that a free-consumer debris tool is not a business — which reinforces positioning this project as data infrastructure / portfolio moat, not a free web toy.

### 2.7 NorthStar Earth & Space — space-based SSA
- **What:** Montreal-based, space-based optical SSA; ~$134M raised (Series D). Appointed a US Operations head 2025-12. Arbitration dispute with Spire (initiated 2024-09, revised to $45.9M 2025-02). **No bankruptcy found** — the "shutdown" rumor is `[unverified / appears false]`. https://tracxn.com/d/companies/northstar-earth-space/ (accessed 2026-07-10)

### 2.8 Vyoma, Aldoria, Digantara — the space-based/optical SSA cohort (2024-2025)
- **Vyoma** (Munich): +€5M (EIF-backed) → €16M+ total; first space-based assets slated early 2025. https://www.vyoma.space/news-items/vyoma-secures-an-additional-5-million-euros-from-eif-backed-space-fund (2024; accessed 2026-07-10)
- **Aldoria** (ex-Share My Space, France): €10M Series A closed 2024-01-23, €22M total; 6→12 optical telescopes by 2025. https://spacenews.com/french-ssa-startup-aldoria-raises-10-9-million/ (2024-01; accessed 2026-07-10)
- **Digantara** (India): **$50M Series B, 2025-12-16** ($64.5M total), expanding from SSA into missile tracking; SCOT tracking sat launched 2025-01. https://spacenews.com/digantara-raises-50-million-to-expand-from-space-surveillance-to-missile-defense/ (2025-12; accessed 2026-07-10)
- **Overlap:** all sensing/tracking; none on identity/provenance. The 2025 money is flowing to *sensors and defense*, not to catalog reconciliation — leaving the master-data lane conspicuously unfunded.

### 2.9 SpaceNav — `[unverified]`: a smaller SSA/astrodynamics services firm (conjunction assessment, orbit determination). No 2025-26 development confirmed in this pass.

### 2.10 Analytics / consulting (the "Headset for space" comparables)
- **BryceTech:** free, citable market briefings — the journalist/policy go-to. 2025: 325 orbital launches, 4,544 spacecraft deployed; SpaceX ~50% of 2025 launches. https://brycetech.com/reports (accessed 2026-07-10); https://www.satellitetoday.com/launch/2026/04/10/brycetech-report-shows-spacex-accounted-for-50-of-launches-in-2025/ (2026-04-10)
- **Novaspace (ex-Euroconsult):** subscription Intelligence Hub; 12th Space Economy Report (2026-01) pegged the 2025 space economy at **$626.4B → $1.01T by 2034**. https://nova.space/hub/product/space-economy-report/ (accessed 2026-07-10)
- **Quilty Space, Payload Research, Analysys Mason/NSR:** subscription equity/market research. https://newspaceeconomy.ca/2026/04/07/directory-of-organizations-that-provide-space-economy-market-intelligence-reports/ (2026-04-07)
- **Overlap:** they sell *narrative + numbers from proprietary databases*. This project's operator-benchmark metrics ride on an *open, queryable, provenance-tracked* graph — the machine-readable substrate these firms build by hand. Different product, adjacent buyer.

### 2.11 Public-sector shift worth noting
**TraCSS (US Office of Space Commerce)** is standing up the civil open-architecture data repository mandated by SPD-3, publishing data under **CC0-1.0**; updated specs 2026-01-22; as of 2026-06, 52 pilot users + 2 national accounts (UK, Australia), 11,125 satellites. https://space.commerce.gov/traffic-coordination-system-for-space-tracss/ (accessed 2026-07-10). This *raises* the value of a clean identity/normalization layer on top of newly-open conjunction/catalog data — and Space Force reportedly opposed proposed budget cuts to it (https://www.airandspaceforces.com/space-force-opposes-to-cutting-tracss-program-from-commerce-budget/ , 2026; accessed 2026-07-10) `[budget-cut politics still in flux]`.

---

## 3. Gap analysis

### 3.1 What (apparently) NOBODY else does — hypotheses tested
1. **Open, provenance-tracked cross-catalog identity with temporal ownership — CONFIRMED as unique (strong).** No open project found maintains SCD2 ownership + per-attribute `source_assertion` + `merge_log` across SATCAT/GCAT/UCS. GCAT and DISCOS curate a single reconciled answer; Seradata does it commercially and closed. The *open + auditable + temporal + conflict-exposing* combination is unmet. (Caveat: "could not find," §1.9.)
2. **Conflict reporting as a first-class product — CONFIRMED as unusual.** Everyone hides disagreement behind a curator's resolved value. Exposing "SATCAT says +, GCAT says decayed, confidence 0.7, here's the merge decision" as a queryable report is genuinely rare. This is the strongest single differentiator and the hardest for a curated DB to copy (it contradicts their "we give you the answer" value prop).
3. **Operator benchmarking from public element sets — CONFIRMED novel in the open.** No public source does operator-vs-operator benchmarking on clean, provenance-tracked identities (`docs/SPEC.md` §1). Bryce/Novaspace benchmark markets from proprietary data; nobody open benchmarks operators from OMM + a clean operator graph.
4. **Auditable merge logs — CONFIRMED as a data-engineering signature, not a product category elsewhere.** This is the MDM/lineage vocabulary (SCD2, provenance, canonical taxonomy) that no space incumbent foregrounds — and that generic data-platform hiring managers read as JD keywords.

### 3.2 What incumbents do that this project cannot
- **Sensing:** radar (LeoLabs), optical (ExoAnalytic, Aldoria, Vyoma, NorthStar), space-based tracking (Digantara). This project owns *no sensor* and will never produce an independent catalog.
- **Conjunction assessment / collision avoidance / maneuver planning:** COMSPOC, Kayhan, Slingshot. Requires covariance/physics this project deliberately does not do.
- **Classified / analyst objects:** only the SeeSat-L amateur community (~200 classified objects, ~18,000 obs/yr from 21 observers) and governments track the dark catalog. https://www.satobs.org/seesat/seesatindex.html (2026-07-10). Out of scope here.
- **Real-time, sub-daily authoritative updates:** gated by CelesTrak/Space-Track politeness; this project is a once-or-twice-daily analytic mirror by design.

### 3.3 Future-work whitespace — ranked by (novelty × feasibility for a solo builder)

| Rank | Whitespace | Novelty | Solo-feasible | Who uses it | What exists today | Why it's open |
|---|---|---|---|---|---|---|
| 1 | **Agent/MCP-native catalog intelligence** — expose the identity graph + conflict report as an MCP server so an LLM can ask "who owns 46112 and does anyone disagree." | Very high | High | Analysts, journalists, other agents, hiring-manager demos | **Nothing.** Search returned zero space-catalog MCP servers. https://kanerika.com/blogs/mcp-context-aware-ai-agents/ (2026; accessed 2026-07-10) | The data is public; nobody has wrapped a *reconciled, provenance-aware* space catalog in the agent protocol. Pure execution gap. |
| 2 | **ITU SNL/SNS ↔ FCC IBFS ↔ NORAD crosswalk** — link regulatory filing identities (USASAT-NGSO-3B, call signs) to physical objects. | Very high | Medium | Spectrum lawyers, regulators, market-access analysts, operators | ITU Space Explorer and FCC IBFS/fcc.report exist **separately**; no public crosswalk connects filing → object. https://www.itu.int/itu-r/space/apps/public/spaceexplorer/networks-explorer , https://fcc.report/IBFS/ (2026-07-10) | Both sides are public but structurally disjoint; joining them is exactly this project's identity-graph competency (already deferred in `docs/SPEC.md` §4.8). |
| 3 | **Status-transition time series** — ACTIVE→GRAVEYARD→DECAYED transitions from `satellite_status_history`, as a temporal analytics product (deorbit compliance, graveyard-timing benchmarking). | High | High | Regulators, insurers, ESG desks, sustainability researchers | Raw decay records exist (Space-Track `decay`); nobody publishes clean *status-transition* series across a reconciled graph. | Data already in the schema; it's a metrics/aggregation build, not new ingestion. |
| 4 | **Debris/fragmentation attribution** — tie ESA fragmentation events / breakups back to owner + operator lineage. | High | Medium | Insurers, litigators, policy, ESG | ESA fragmentation DB exists (https://fragmentation.esoc.esa.int/home , 2026-07-10); attribution-to-current-owner (post-M&A) is unautomated. | The M&A lineage that makes attribution correct is this project's core asset. |
| 5 | **Insurance / ESG scoring layer** — operator risk/sustainability scores from status transitions + ownership + conjunction exposure. | Medium-high | Medium | Space insurers (a named Seradata buyer), ESG funds | Seradata serves insurers with a closed DB; no open scoring exists. https://www.slingshot.space/product-seradata (2026-07-10) | Rides on layers 3+4; open substrate + transparent methodology is a differentiator vs. black-box incumbents. |
| 6 | **Launch-market analytics** — launches/deployments by operator/lineage over time. | Medium | High | Journalists, analysts, BD teams | BryceTech/Novaspace do this from proprietary data. https://brycetech.com/reports (2026-07-10) | Crowded on the *output*, but doing it transparently from an open graph is the wedge; lower novelty because incumbents cover the narrative. |

**Top-3 for this builder:** (1) MCP-native access — unmatched novelty, trivial to demo, and it's the current hiring narrative; (2) ITU/FCC crosswalk — highest domain-defensibility, directly extends the identity graph; (3) status-transition time series — near-zero marginal ingestion, high analyst value.

---

## 4. Who to talk to — shortlist (this project as a door-opener)

1. **Slingshot Aerospace — Seradata/SpaceTrak product & data team.** Hook: "I built the open, provenance-tracked, conflict-exposing version of SpaceTrak's identity layer as a portfolio piece — here's the merge log you can't get from a curated DB." Direct strategic mirror. https://www.slingshot.space/product-seradata (2026-07-10)
2. **Office of Space Commerce — TraCSS data/architecture team.** Hook: "Your CC0 open repository needs a normalization/identity layer on top; I have a working one over SATCAT/GCAT/UCS." Public, open-mandated, actively onboarding. https://space.commerce.gov/traffic-coordination-system-for-space-tracss/ (2026-07-10)
3. **CelesTrak / Dr. T.S. Kelso.** Hook: "I'm consuming your SupGP cross-tag anomalies as entity-resolution assertions — here's what the mismatch flags reveal at scale." Respectful, source-attributing, technically fluent. https://celestrak.org/satcat/ (2026-07-10)
4. **Jonathan McDowell (GCAT / CfA).** Hook: "I turned SATCAT↔GCAT disagreements into a machine-readable conflict corpus with confidence scores — want to see where the catalogs diverge most?" Flatters the scholar, surfaces genuinely useful reconciliation. https://planet4589.org/space/gcat/ (2026-07-10)
5. **LeoLabs — data/catalog product.** Hook: "You now feed TraCSS and UDL a commercial catalog; I built the ownership/identity layer that turns object IDs into operator intelligence." Complementary, not competitive. https://leolabs.space/ (2026-07-10)
6. **Kayhan Space — Pathfinder team.** Hook: "Your CA output is keyed on RSO IDs; I can tell you *whose* objects are in each conjunction and their M&A-correct current owner." Enriches their alerts. https://www.kayhanspace.com/ (2026-07-10)
7. **A space insurer / Lloyd's space syndicate.** Hook: "Seradata is your closed data source; here's a transparent status-transition + ownership-lineage feed for deorbit-compliance and risk scoring." Insurers are a named Seradata buyer, so the need is proven. https://www.slingshot.space/product-seradata (2026-07-10)
8. **Payload / Quilty / BryceTech analysts.** Hook: "I have an open, queryable operator-lineage graph — the machine-readable substrate under the charts you publish by hand." Content partnership / data-supplier angle. https://brycetech.com/reports (2026-07-10)

---

### Sourcing honesty
- **Corrected during research:** Slingshot's Seradata acquisition was **2022-08-03**, not 2024 (the prompt's framing) — verified via SpaceNews.
- **`[unverified]`:** Slingshot "Global Data Marketplace" and layoffs; Kayhan 2025 raise; SpaceNav 2025-26 developments; any NorthStar bankruptcy (appears false — company still operating, arbitration ≠ insolvency).
- **Weakest claim by design:** §3.1 "nobody does X" rests on exhaustive search returning nothing (§1.9). Absence of evidence, treated as strong-but-rebuttable — a curated internal tool at a defense contractor could exist unpublished.
