# Exoplanet Hunter — Can This Engine Make a Genuine Contribution to Exoplanet Discovery/Vetting?

**Framing:** passion project, optimized for novelty / coolness / real contribution — explicitly **not** money (the money verdict lives in `docs/research/exoplanets.md` §4b: exoplanets have ~zero commercial market; this is a credibility/collaboration/joy play).
**Engine under test:** change-point/signal detection on noisy astronomical time-series at scale; cross-catalog entity resolution with per-attribute provenance; AI-orchestrated research dossiers (agents gather cited evidence, human adjudicates) with a gold-standard accuracy program; cheap columnar time-series analytics; MCP-native delivery; Python; can orchestrate frontier AI agents to build in days.
**Hypothesis tested:** *"The field's bottleneck isn't FINDING transit candidates, it's VETTING them — and an AI-dossier + cross-archive entity-resolution + provenance engine is a novel, genuinely useful contribution."*
**Research date:** 2026-07-15. Every load-bearing claim carries a URL + access date; unverifiable claims are tagged `[unverified]`.

**One-paragraph verdict up front.** The hypothesis is *half right, and the correction makes it stronger.* Finding candidates is genuinely open in exactly one lane (single-transit / long-period signals that the automated pipelines discard by design). Pure *statistical/photometric* vetting is **not** an open bottleneck — it is being aggressively automated by ML classifiers (LEO-Vetter, ExoMiner++, RAVEN) and is closing fast. The real, un-automated, under-tooled layer is **evidence synthesis**: pulling the DV report, ExoFOP dispositions, imaging/spectroscopy notes, cross-archive identity, and prior literature into a coherent, cited per-candidate *case*. That layer is where the engine's four differentiators (entity resolution + provenance + AI-orchestrated cited dossiers + a gold-standard accuracy program) map almost one-to-one — and the novelty check comes back clean: **no published LLM/agent system does exoplanet candidate dossiers today.**

---

## 1. Data-access reality (mid-2026): what an individual can actually get for free

**TESS is alive, healthy, and still pouring out data.** It is in its **third extended mission (EM3), planned Sept 2025 – Sept 2028** (https://heasarc.gsfc.nasa.gov/docs/tess/notice-of-call-for-community-input-into-the-tess-extended-mission-planning.html, 2026-07-15), confirmed extended by the June-2025 Astrophysics Senior Review and by the existence of an active **Cycle 9 Guest Investigator call** (https://heasarc.gsfc.nasa.gov/docs/tess/docs/TESS_Cycle9_D3CS.pdf, 2026-07-15). As of mid-July 2026 it is in **Cycle 8** on the southern ecliptic: **Sector 105 ended 2026-07-11; Sector 106 runs 2026-07-11 → 08-09; Sector 107 runs 08-09 → 09-07** (https://heasarc.gsfc.nasa.gov/docs/tess/sector.html, 2026-07-15). Cadence remains **~27 days/sector**, new sectors still delivered, no fuel/health alarms. The exact Senior-Review ranking language is `[unverified]` (report PDF would not render), and out-year funding carries political risk from the FY2026 budget request (https://aas.org/posts/news/2025/06/fy26-presidents-budget-request, 2026-07-15) — but operationally, TESS is producing.

**The data products, and their asymmetry (this asymmetry is the whole opportunity).** SPOC produces **2-minute** light curves for only tens of thousands of pre-selected stars per sector (later extended toward ~160,000 via FFI-selected targets) and **20-second** for ≤1,000 (https://archive.stsci.edu/hlsp/tess-spoc, 2026-07-15). Everyone else — **millions of stars** — appears only in the **Full-Frame Images** (now **200-second** cadence in the extended mission, downlinked ~weekly), served as light curves by MIT's **Quick Look Pipeline (QLP)**: e.g. **5.7 million unique stars** in one EM1 year, **9.1 million** in another (https://iopscience.iop.org/article/10.3847/2515-5172/aca158, 2026-07-15; https://iopscience.iop.org/article/10.3847/2515-5172/ac2ef0, 2026-07-15). **Everything is free on MAST**, pulled with the open-source **`lightkurve`** Python package (https://archive.stsci.edu/missions-and-data/tess, 2026-07-15). **Kepler and K2 remain fully public on MAST** with community HLSPs (EVEREST/K2SFF/K2SC) reachable through the same tool.

**The rest of the sky, briefly:** **PLATO slipped** — spacecraft complete (Oct 2025), passed thermal-vacuum (Apr 2026), now launching **January 2027** to L2; no science data yet (https://cnes.fr/en/projects/plato, 2026-07-15). **Vera Rubin / LSST began its 10-year survey 2026-06-30** (https://rubinobservatory.org/news/action-rubin-lsst-begins, 2026-07-15); crucially, the **alert stream is public via nine community brokers — ALeRCE, ANTARES, Fink, Lasair, et al. — free to anyone**, while the raw pixel/catalog data (DP1/DR1) is rights-restricted to US+Chile scientists (https://noirlab.edu/public/news/noirlab2605/, 2026-07-15). Rubin is *sparse* for transit work (~800 visits/object over 10 years), so it is not a planet-transit firehose — its exoplanet relevance is microlensing and stellar characterization. **Gaia DR4 is not out yet** (ESA: "no earlier than mid-2026"; community estimate ~Dec 2026, `[unverified]` exact month) and will deliver the first large batch of **astrometric giant-planet candidates** plus all epoch photometry (https://www.cosmos.esa.int/web/gaia/dr4, 2026-07-15; https://astrobiology.com/2026/05/the-upcoming-release-of-gaia-dr4-will-yield-thousands-of-giant-planet-candidates.html, 2026-07-15).

**Bottom line for an individual:** TESS 2-min + 20-s SPOC light curves, QLP/TESS-SPOC FFI light curves for millions of stars, all Kepler/K2, plus the entire ExoFOP-TESS candidate corpus and DV reports — **all downloadable, license-clean, laptop-scale**. Data access is *not* the constraint. This matters: it means the only thing standing between an outsider and a contribution is *what you do with the data*, not access to it.

---

## 2. What independents have genuinely achieved (and how they got credited)

Outsiders have a **real, well-trodden** track record — overwhelmingly in the single-transit / long-period regime the pipelines miss.

**Planet Hunters TESS (PHT)**, led by Nora Eisner (Zooniverse, 43,000+ volunteers): as of Feb 2024 it had produced **183 community TOIs (cTOIs), 109 promoted to official TOI, and 19 confirmed planets** (https://arxiv.org/pdf/2505.00898, 2026-07-15). Named finds: **TOI-813 b** (84-day Saturn on a subgiant, PHT's first; https://arxiv.org/abs/1909.09094); **TOI-1338 b** (TESS's first transiting circumbinary planet, first spotted on the PHT *forum*, with **six volunteer co-authors**; https://arxiv.org/pdf/2004.07783); **HD 152843 b/c** (SPOC had *merged two planets' transits into one threshold-crossing event and did not promote it* — citizen scientists caught all three transits; https://arxiv.org/pdf/2106.04603); **TOI-4633 c "Percival"** (~272-day mini-Neptune in the optimistic habitable zone, second-longest-period TESS planet; uploaded as a cTOI 2020-05-27, promoted 2022-12-14, ~15 volunteers flagged it; https://arxiv.org/abs/2404.18997, 2026-07-15).

**The Visual Survey Group (VSG)** — the Tom Jacobs / Daryll LaCourse / Martti Kristiansen / Saul Rappaport / Andrew Vanderburg Pro-Am collaboration — has **visually surveyed nearly 10 million light curves and authored 69 peer-reviewed papers** using by-eye inspection (the `LcTools` software) to catch what Box-Least-Squares pipelines discard (https://arxiv.org/abs/2205.07832, 2026-07-15). Finds include **HD 139139 "the Random Transiter"** (two dozen aperiodic dips, no period, invisible to BLS; https://academic.oup.com/mnras/article/488/2/2455/5525096), the first **sextuply-eclipsing sextuple star system** (https://arxiv.org/abs/2101.03433), and **single-transit giants handed off for RV confirmation** (TOI-2010 b, TOI-6692 b). Amateurs appear as **direct co-authors** on these AAS/MNRAS papers.

**The CTOI process** (ExoFOP-TESS, run by NExScI/Caltech): **registration is open to anyone** — request an account, agree to the conduct policy, submit a candidate, which becomes a **cTOI**; the TOI team reviews and, if it meets standards, assigns a **TOI number** (https://tess.mit.edu/followup/exofop-tess/, 2026-07-15). Scale as displayed: **~3,964 CTOIs, 8,064 TOIs** (https://exofop.ipac.caltech.edu/tess/, 2026-07-15). PHT's promotion rate is a clean data point: **109/183 ≈ 60% of its cTOIs were promoted to TOI**. **⚠️ Live constraint: ExoFOP paused new CTOI uploads as of 2026-03-31, pending updated candidate guidelines** (https://exofop.ipac.caltech.edu/tess/, 2026-07-15) — verify status before relying on that channel; it may reopen with stricter, dossier-friendlier requirements (which would *help* a dossier tool).

**Documented pipeline misses** (the white space, proven): SPOC requires a multi-event statistic above threshold and rejects lone transits, so single-transit/long-period systems slip through — **TOI-6692 b** (eccentric ~130-day giant, single transit, VSG + PHT recovered it; https://arxiv.org/pdf/2601.16357), **TOI-1899 b** (warm Jupiter, single transit below SPOC's MES threshold, PHT-recovered; https://arxiv.org/pdf/2007.07098), and the HD 152843 mis-merge above.

**Credit pathways, low → high barrier:** (1) **cTOI → TOI promotion** (mission-level credit); (2) **RNAAS — Research Notes of the AAS** — the lowest-barrier venue: editor-checked, not peer-reviewed, published in ~72 hours, free, DOI + ADS-indexed, explicitly for single-object announcements (https://journals.aas.org/research-notes/, 2026-07-15); (3) **Zooniverse co-authorship** (qualifying volunteers become named alphabetical co-authors); (4) **TFOP SG1 (seeing-limited photometry) explicitly recruits advanced amateurs** and observers routinely become co-authors (https://heasarc.gsfc.nasa.gov/docs/tess/tfop.html, 2026-07-15); (5) **join a Pro-Am group** and feed candidates to professionals. **The infrastructure is deliberately open.** An outsider *can* get their name on a discovery.

---

## 3. The vetting bottleneck — the crux, examined honestly

**How vetting actually works.** SPOC (NASA Ames) processes ~92% of survey targets → **Threshold Crossing Events (TCEs)** → the **Data Validation (DV)** module runs odd/even depth tests, a bootstrap significance, a "ghost" core-vs-halo diagnostic, and a **difference-image centroid** to localize the transit source, emitting a **DV report** (https://arxiv.org/abs/2002.00691, 2026-07-15). Human vetters at the **MIT TESS Science Office** group-vet TCEs → **TOIs** (https://tess.mit.edu/toi-releases/, 2026-07-15). The ladder: *TCE → TOI (worth follow-up) → PC (no FP found) → VP (statistically validated, no mass) → CP (mass measured)*. **TFOP** coordinates follow-up in five sub-groups: **SG1** seeing-limited photometry, **SG2** recon spectroscopy, **SG3** high-res imaging, **SG4** precise RV, **SG5** space photometry (https://heasarc.gsfc.nasa.gov/docs/tess/tfop.html, 2026-07-15).

**The false-positive problem is real and expensive.** Astrophysical mimics — eclipsing binaries (EBs), background/blended EBs, hierarchical-triple EBs — plus instrumental systematics. In the Prime Mission, **565 of 2,241 TOIs (~25%) were dispositioned false positive** by follow-up, with an underlying FP rate of **~15–47%** depending on planet size (https://arxiv.org/pdf/2103.12538, 2026-07-15). Disambiguating an on-target planet from a blended EB needs pixel-level centroid work plus scarce ground assets.

**But here is the honest correction to the hypothesis: statistical/photometric vetting is NOT an open bottleneck — it is being automated, fast.**
- **TRICERATOPS** — Bayesian per-scenario false-positive probabilities; the community successor to the now-**deprecated vespa** (author recommends retiring it) (https://arxiv.org/abs/2002.00691; https://github.com/timothydmorton/VESPA, 2026-07-15).
- **LEO-Vetter** (Kunimoto 2025) — fully automated flux- *and* pixel-level vetting, **91% completeness, 97% reliability**, open source (https://arxiv.org/abs/2509.10619, 2026-07-15).
- **RAVEN** (2026) — reprocessed TESS-SPOC FFIs → **100+ newly validated planets, 2,000+ vetted candidates** (https://academic.oup.com/mnras/article/548/3/stag512/8528996, 2026-07-15).
- **ExoMiner** (2021, explainable DNN) validated **~300+ Kepler planets**; **ExoMiner++** (AJ, published 2025-10-27) transfer-learned to 2-min TESS, and on **147,568 unlabeled TCEs flagged 7,330 as PCs including 50 *new* CTOIs**; **ExoMiner++ 2.0** (Jan 2026) extends to FFI TCEs (https://arxiv.org/abs/2502.09790; https://arxiv.org/abs/2601.14877, 2026-07-15). Plus DART-Vetter, Astronet, GPFC, ExoNet, WATSON-Net — **all CNN/attention/tree classifiers, none LLM.**

**So where is the *real* bottleneck?** Two places, and only one is open to an outsider:
1. **RV mass confirmation** — high-precision RV instruments are "heavily oversubscribed… too many planets, too few telescopes" (https://arxiv.org/pdf/2202.03656, 2026-07-15). This is a **hardware/telescope-time** constraint. **An outsider with software cannot fix it.** Do not aim here.
2. **Evidence synthesis / the vetting *case*** — pulling the DV diagnostics, ExoFOP TFOP dispositions (50+ codes), imaging/spectroscopy notes, cross-archive identity, and prior literature into a coherent, cited per-candidate dossier. **This is stubbornly manual, under-tooled, and non-LLM-occupied.** ~10,000 candidates await validation (https://arxiv.org/html/2512.00967, 2026-07-15), each needing exactly this kind of evidence assembly. **This is the engine's sweet spot.**

**THE NOVELTY CHECK (definitive).** After hard searching — "LLM exoplanet vetting," "AI agent exoplanet candidate," "GPT false positive," "agentic astronomy literature," "exoplanet dossier," RAG-astronomy, StarWhisper, AstroLLM, cmbagent, AstroAgents, NASA FDL — the landscape partitions cleanly:
- **(a) ML/DL classifiers** — exist, workhorses, **not LLM** (all of the above).
- **(b) General astronomy LLM/agents** — exist, **not vetting**: **StarWhisper Telescope** (LLM agent for *observation scheduling/telescope control*, on-sky since Oct 2024; https://www.nature.com/articles/s44172-025-00520-4, 2026-07-15); **AstroMLab** (specialized astronomy LLM); a wave of agent *benchmarks* (Stargazer, gwBenchmarks, ReplicationBench) and the cautionary *"Plausible but Wrong: Agentic Failures in Astrophysical Workflows"* (https://arxiv.org/html/2604.25345, 2026-07-15).
- **(c) LLM-agentic *exoplanet* tools** — exactly **one** exists, and it is **not vetting**: **ASTER (Agentic Science Toolkit for Exoplanet Research)**, arXiv **2603.26953** (March 2026), whose verbatim abstract scope is **atmospheric characterization** — an LLM agent orchestrating Exoplanet-Archive queries, **TauREx radiative transfer, and Bayesian atmospheric retrieval** on transmission spectra (https://arxiv.org/abs/2603.26953, 2026-07-15). **The closest architecture, AstroAgents** (multi-agent with a Semantic-Scholar "literature reviewer" + a "critic"), does **astrobiology hypothesis generation from mass-spec data, not exoplanet candidate vetting** (https://arxiv.org/abs/2503.23170, 2026-07-15).

**Definitive conclusion:** As of mid-2026, **no published LLM- or AI-agent system performs exoplanet-candidate vetting or generates cited literature/evidence dossiers for specific TOIs/candidates.** The nearest neighbors each miss the target on a different axis (ASTER = atmospheres; AstroAgents = astrobiology; StarWhisper = scheduling). The specific concept — **an agentic, retrieval-grounded, cited-evidence dossier that assembles the vetting case for a given candidate, with a gold-standard accuracy program** — appears **genuinely unoccupied.** (Caveat: this is an *absence-of-evidence* negative — strong but inherently `[unverified]` as a universal claim; an unpublished internal notebook could exist. That the space is *entered* at all — ASTER, March 2026 — means the window is open now, not forever.)

---

## 4. Candidate white space — which hunt is most tractable *and* novel for solo + AI

| Rank | Lane | Novelty | Solo+AI feasible | State of the art | Verdict |
|---|---|---|---|---|---|
| **1** | **AI cited-evidence *vetting dossiers* + provenance for active candidates** (the evidence-synthesis layer) | **Very high** — novelty check is clean | **High** (data all public; N is bounded: ~8k TOIs, ~4k CTOIs) | Manual, human-assembled; ML classifiers give a *score* but not a *cased dossier* | **Lead here.** Engine maps 1:1 |
| **2** | **Single-transit / long-period hunting** (monotransit/duotransit, multi-sector stitching) | High | **High** (proven by VSG/PHT) | ~1,100 monotransits predicted; 88 mono + 85 duo announced; **avg 38 period aliases** per duotransit (https://arxiv.org/html/2604.09254v1, 2026-07-15); MonoTools/NGTS/CHEOPS follow-up | **The one open FINDING lane.** Combine with #1 |
| **3** | **Cross-archive entity resolution + per-attribute provenance** (TOI/CTOI/KOI/EPIC/TIC/Gaia mess) | Medium-high | High | **Exo-MerCat v2.0.0** merges NASA+EU+KOI+TOI+EPIC but **flattens to one value**, no per-attribute provenance (https://arxiv.org/abs/2502.08473, 2026-07-15); TIC still on Gaia DR2, DR3 crossmatch is an active 2026 problem (https://arxiv.org/pdf/2603.28850, 2026-07-15) | **The connective tissue** under #1 and the re-ranking project — real but has an incumbent; differentiate on *provenance* + *candidate-level* |
| 4 | **FFI reprocessing QLP under-serves** | Low-medium | Medium | **Crowded**: RAVEN, ExoMiner++ 2.0, QLP itself, TESS Triple-9 | Don't lead — you'd race funded ML teams |
| 5 | **TTV mining** (non-transiting companions) | Low-medium | Medium | Now systematized: *TTV in TESS: Catalog from the First Five Years* (https://arxiv.org/html/2606.17218, 2026-07-15) | Being closed |

**The single most exciting genuinely-open niche is #1**, and #2 is the finding-side complement that feeds it. Long-period/single-transit candidates are (a) the documented pipeline blind spot, (b) proven outsider territory, and (c) *exactly* the candidates that need the most evidence-synthesis work (period-alias disambiguation, multi-sector stitching, host-star cross-ID, follow-up triage) — so a dossier engine and a long-period hunt are the same project viewed from two ends.

---

## 5. Versus the re-ranking project — and why they're one graph

`docs/research/exoplanets.md` already worked out the **re-ranking project**: provenance-tracked re-ranking of JWST TSM/ESM + HWO habitability targets — *"does the target rank flip depending on which catalog/publication you trust?"* — anchored on **Kane 2014** (a ~5% T_eff error → ~10% HZ-boundary shift; HZ *membership* can flip; https://arxiv.org/abs/1401.3349, 2026-07-15), landing on the **HWO Target Stars & Systems (TSS25)** living list (164 Tier-1 / 659 Tier-1+2 stars; https://arxiv.org/abs/2509.20544, 2026-07-15). Its novelty rests on a "could-not-find" (nobody tabulates top-25 rank-order churn as a function of provenance); its audience is the HWO START team, ExoPAG, JWST proposers.

**Which is the stronger *contribution*, and which is *cooler*?**

| Dimension | Vetting-dossier (this doc, #1+#2) | Re-ranking (exoplanets.md) |
|---|---|---|
| **Novelty** | **Higher** — definitively unoccupied AI niche; ML gives scores, nobody builds cased dossiers | High but "could-not-find"; adjacent to Exo-MerCat + ASTER's re-rank ambitions |
| **Credit pathway** | **Clearer & faster** — cTOI→TOI, RNAAS in 72h, PHT/VSG/TFOP co-authorship | Slower — a methods/target paper, co-authorship via HWO working groups |
| **Public "cool" artifact** | **Higher** — an open, live dossier site for *every active TOI*, plus your own first CTOI | A conflict-report notebook / target-churn table (rigorous, less visceral) |
| **Rigor as a single result** | Broad but softer | **Higher** — one sharp, quantified scientific claim (rank churn) |
| **Audience size** | Larger (every follow-up observer, every TFOP participant, PHT/VSG) | Small, elite (a few dozen HWO/JWST target-setters) |

**Recommendation: the vetting-dossier project is the stronger *contribution* and the cooler *artifact*; the re-ranking project is the more rigorous single scientific *paper*.** And they **should combine** — they are literally the same provenance graph viewed at two altitudes:

> **One provenance graph over TIC / TOI / CTOI / KOI / EPIC / Gaia DR3**, with per-attribute `source_assertion` + auditable `merge_log`, feeds **both**: (a) **candidate-level vetting dossiers** (does this signal survive the evidence?), and (b) **target-level re-ranking** (does this confirmed planet's JWST/HWO priority flip under a different source?). The entity-resolution engine is the shared substrate; the dossier layer and the re-rank layer are two consumers of it. Build the graph once; ship two products.

This is the strongest overall shape: it de-risks the re-ranking project's thin novelty by wrapping it in the dossier project's clear credit pathway, and it gives the dossier project scientific depth (habitability/observability metrics with provenance) that no ML classifier has.

---

## 6. Adjacent option, same engine — Rubin/LSST asteroid linking (tracklet linking = entity resolution)

Rubin's asteroid flood is real: **First Look (2025) reported ~2,100 new asteroids; on 2026-04-02 Rubin submitted >11,000 never-before-seen asteroids** (33 NEOs) in one batch; the 10-year projection is **>5 million** new solar-system objects (https://rubinobservatory.org/news/rubin-first-look/swarm-asteroids, 2026-07-15; https://phys.org/news/2026-04-early-vera-rubin-observatory-reveals.html, 2026-07-15). **Tracklet linking *is* entity resolution** — form tracklets, link across nights, fit orbits.

**Honest comparison for this builder:**
- **More open than exoplanet vetting on data + credit:** MPC data (MPCORB, the **Isolated Tracklet File** of ~4M unlinked tracklets) is fully public; credit is *automatic* via **MPC provisional designations**; existence proof — independent researcher **Ben Engebreth earned ~795 MPC-designated objects** solo by linking ITF tracklets (https://www.benengebreth.org/dynamic-sky/itf-linking/, 2026-07-15); open tools exist (**FindPOTATOs**, https://iopscience.iop.org/article/10.3847/PSJ/ad94eb, 2026-07-15); citizen science exists (**The Daily Minor Planet**, 1,200+ objects).
- **But the nightly-linking core is *saturated*:** Rubin's Solar System Processing runs **HelioLinC3D** on a 24-hour cycle and auto-submits to MPC; the MPC runs its own identifications pipeline every 5 minutes (~1,000 linkages/day) (https://dp1.lsst.io/processing/moving/ss_linking.html, 2026-07-15). The pure "link tonight's tracklets" job is taken.
- **The real opening is downstream/archival:** **NEOCP triage** (Rubin posts ~129 candidates/night, only ~8.3% true NEOs, most too faint for amateur follow-up — a *prioritization* problem) and **archival ITF entity-resolution** (the unsolved "3-nighter" mislinkage problem) (https://iopscience.iop.org/article/10.3847/1538-3881/adc89f, 2026-07-15 `[abstract-only, full text 403]`).

**Verdict:** the asteroid lane has a **better-defined, faster, more automatic credit pathway** (MPC designations) than exoplanets, and the engine's entity-resolution core fits tracklet linking directly. But (a) the linking core is more saturated by professional pipelines, and (b) it is *less* aligned with the "coolness" of *finding life* — asteroids are a NEO-defense / small-body-science story, not a habitability story. **Keep it as a strong hedge / second artifact**: if the CTOI-upload pause (§2) or TFOP social friction stalls the exoplanet path, archival ITF linking is a cleaner solo "get designations" win. But for a *passion* project optimized on coolness, exoplanets win.

---

## 7. The recommended project — spec, MVP, credit pathway, success

### The project: **"ExoDossier" — an open, AI-generated cited vetting-dossier engine for TESS candidates, over a provenance graph that also re-ranks targets.**

Pick the combined shape from §5. Lead with the dossier + long-period hunt; build the entity-resolution/provenance graph as the shared substrate; keep the re-ranking layer as the second consumer.

### The ~1–2 week MVP (individual + frontier AI agents)

Deliberately scope to a **focused, high-value slice**, not all 8,000 TOIs at once: **the single-transit / long-period TOIs + the "APC" (ambiguous planet candidate) dispositions** — the under-vetted, pipeline-blind-spot population where evidence-synthesis pays most.

1. **Entity-resolution substrate (reuse the identity engine).** Resolve each target across **TIC 8.2 / TOI / CTOI / KOI / EPIC / Gaia DR3 / HD / HIP / 2MASS** via SIMBAD main-ID + positional fallback; store a per-attribute `source_assertion` (host T_eff, R★, distance, and candidate period/depth/duration) with uncertainty and provenance. Programmatic ExoFOP access via **`etta`** (https://etta.readthedocs.io/, 2026-07-15). *Exercises entity resolution + provenance on real disagreement; explicitly goes beyond Exo-MerCat by keeping the conflict instead of flattening it.*
2. **Signal-detection pass (reuse the change-point engine).** Pull the MAST light curve with `lightkurve`; independently recover the transit; for monotransits, **stitch multiple sectors** to search for a second transit and constrain the period (the alias-collapse win). *Exercises the time-series pillar; produces an original datum, not just a literature scrape.*
3. **Statistical scenarios.** Run **TRICERATOPS** for per-scenario FP probabilities; surface the SPOC **DV report** diagnostics (odd/even, centroid, ghost). *Grounds the dossier in the field's accepted vetting vocabulary.*
4. **AI-orchestrated cited dossier (the novel core).** Agents gather cited evidence — ADS/arXiv on the host star, ExoFOP TFOP dispositions and notes, imaging/spectroscopy history, catalog cross-IDs — and assemble a structured, human-adjudicated **vetting case**: "here is the evidence for/against planet, each claim cited, each parameter with provenance, confidence stated." *This is the unoccupied niche.*
5. **Gold-standard accuracy program.** Back-test dossier dispositions against the **known** TFOPWG dispositions (KP/CP/FP/PC/APC) on a held-out set; report a real accuracy/calibration number. *This is the differentiator that turns a demo into a credible instrument and answers the "Plausible but Wrong" agentic-failure critique head-on.*
6. **MCP-native delivery.** Expose it as an MCP server so an LLM can ask *"give me the vetting case for TOI-XXXX, and tell me which conclusion flips if I use the discovery-paper stellar radius instead of the Gaia one"* — one query spans dossier + provenance + re-rank.

### The public "cool" artifact
An **open ExoDossier site** with a cited, provenance-tracked dossier for every candidate in the focused slice (then scaling to all active TOIs), each showing its evidence graph, its independent light-curve recovery, its TRICERATOPS scenarios, and its measured accuracy — **plus your own first submitted CTOI** from the long-period hunt. This is more visceral than any notebook: a browsable "case file" per planet candidate.

### The concrete contribution / credit pathway
1. **Find or resurrect a real candidate** in the long-period slice (recover a mis-merged/under-vetted signal like HD 152843, or a fresh monotransit) → **submit as a CTOI → TOI promotion** (once uploads reopen post-2026-03-31 pause; https://exofop.ipac.caltech.edu/tess/, 2026-07-15).
2. **Publish** the tool + a batch of dossiers as **RNAAS notes** (72h, free, DOI-indexed) and/or a methods paper.
3. **Offer ExoDossier as infrastructure** to **Planet Hunters TESS (Nora Eisner)**, the **Visual Survey Group (Kristiansen / Jacobs / LaCourse)**, and **TFOP SG1** → co-authorship on their discovery papers. These communities already assemble dossiers by hand; a good tool is welcome, not competitive.
4. **Cross-sell the provenance graph** to the re-ranking audience — the **HWO START team** (Dressing / O'Meara), **NExScI**, named researchers **Stark / Tuchow / Kempton** — as the same graph feeding target-churn analysis.

### What "success" looks like in 3 months
- An **open ExoDossier site live** with cited dossiers for the full long-period/APC slice (hundreds), each with an independent light-curve recovery and TRICERATOPS scenarios.
- A **published gold-standard accuracy number** for dossier dispositions vs. known TFOPWG labels.
- **At least one submitted CTOI** (or, if uploads are still paused, one **RNAAS** note + a queued CTOI) from the long-period hunt.
- **Warm contact** with at least one of PHT / VSG / TFOP SG1, or a named researcher who finds the tool useful — the seed of a co-authorship or a ROSES ADAP/XRP Co-I relationship.

### Where outsiders are NOT welcome / the bottleneck is imaginary (be clear-eyed)
- **Do not build another FP classifier.** Statistical/photometric vetting is being automated at scale (LEO-Vetter, ExoMiner++, RAVEN). That bottleneck is closing; you would be racing funded teams.
- **You cannot fix the RV/telescope-time bottleneck** — the real hard constraint on *confirmation* — with software. Don't frame the project as solving confirmation.
- **The catalog-*merging* lane has an incumbent** (Exo-MerCat). Differentiate strictly on *per-attribute provenance* + *candidate-level* + *dossier consumption*, not "I merged the catalogs."
- **Live friction: CTOI uploads are paused (2026-03-31).** Have RNAAS as the fallback credit venue and verify ExoFOP status before promising a CTOI.
- **This is not money.** Exoplanets have ~zero commercial market (`exoplanets.md` §4b). The return is credibility, collaborators, a genuinely novel public artifact, and the joy of contributing to the search for other worlds — which is exactly the stated objective.

---

## Sourcing honesty
- **Strongest finding for the project:** the vetting *evidence-synthesis* layer is un-automated *and* the AI-agent niche is definitively unoccupied (ASTER = atmospheres, AstroAgents = astrobiology, StarWhisper = scheduling; https://arxiv.org/abs/2603.26953, https://arxiv.org/abs/2503.23170, 2026-07-15). This is the load-bearing novelty claim.
- **Strongest correction to the hypothesis:** "vetting is the bottleneck" is only half true — *statistical* vetting is being automated; the open sub-layer is *evidence synthesis*, and the truly-hard bottleneck (RV confirmation) is telescope-time, not software.
- **Most important caveats:** (1) the novelty claim is an absence-of-evidence negative — strong but rebuttable; (2) ExoFOP CTOI uploads are paused as of 2026-03-31 — the primary credit channel is temporarily gated; (3) the FFI-reprocessing and TTV lanes are already crowded — avoid them.
