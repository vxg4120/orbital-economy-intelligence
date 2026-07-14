# The Engine as a Moat, and Time-Series Layers to Build

**Subject:** Orbital Economy Intelligence — (Q1) whether a "cheap engine for very large time-series"
is a credible *additional* moat beyond the data/identity moat, and (Q2) which novel time-series
layers to add on the satellite data, ranked.
**Companion to:** `docs/PRODUCT.md`, `docs/research/landscape.md`.
**Research date:** 2026-07-14. Every load-bearing claim carries a URL + access date; anything
unverifiable is tagged `[unverified]`. Blog/vendor-marketing figures are flagged as such — treat
directional, not audited.

---

## Q1 — Is the cheap time-series engine a real moat?

### 1.1 Quantify honestly: this is *medium* data, not big data

The instinct behind the question is "the full catalog at several element sets per day over years is
billions of rows, and billions of rows is hard." The first half is roughly right; the second half
is wrong by 2026 standards, and being honest about that is the whole point.

The decisive number: **Space-Track's `gp_history` — the complete historical ephemeris archive for
every unclassified object since 1959 — is "over 138 million elsets" in total.**
https://www.space-track.org/documentation (accessed 2026-07-14). That is the entire archive, not a
subset. This project's live fact layer (`gp_elements`, ~9.67M rows per `docs/SPEC.md`) is a recent
window of it.

Reconcile that against the "billions" projection. The catalog is ~70k objects *today*, but for most
of its history it held only a few thousand; TLE cadence was also far lower. So the historical
archive to date is ~10^8 rows, not 10^9. Going *forward*, if the project captures dense GP (every
~2h refresh) for the ~26k+ actively tracked objects, it accrues on the order of 26,000 × 12/day ×
365 ≈ 10^8 rows/year — reaching ~1 billion in roughly a decade. So "billions" is a defensible
*long-run* figure only if you retain high-cadence forward capture; the data on the ground now is
~138M.

Now size it. An OMM/GP row is ~30–40 mostly-numeric fields, call it ~0.3–1 KB. 138M rows is
~40–140 GB uncompressed, and columnar compression on time-series (delta-of-delta on epoch/mean
motion + ZSTD) routinely hits 10–20× — ClickHouse's own internal platform compresses 100 PB down to
5.6 PB (~18×). https://clickhouse.com/resources/engineering/managing-petabyte-scale-logs-without-sampling
(accessed 2026-07-14). So the full archive is **~3–15 GB compressed**. Even the decade-out
billion-row projection is ~30–150 GB compressed. That fits in RAM on a single workstation and is a
rounding error for any columnar engine.

**Honest verdict on scale:** satellite element-set data does *not* justify an exotic or distributed
engine. It is comfortably a single-node problem. A row-store like TimescaleDB with compression
handles it; DuckDB over Parquet handles it on a laptop; ClickHouse would be idling. The place
satellite data *does* start to strain a naive setup is not row count but *access pattern*: full-
history "scan every elset this object ever had and find the change-points" queries across the whole
catalog, run repeatedly and backfilled — which is exactly where columnar layout earns its keep over
a row-store, even at modest total size.

### 1.2 State of the art for cheap time-series at scale (2026)

The 2026 consensus stack for "runs cheaply at huge scale" is not one engine; it is a *pattern*:
**columnar format + aggressive compression + object storage, compute decoupled from storage.**

- **TimescaleDB** — Postgres extension; converts older chunks to a columnar, compressed format.
  Benchmarks put its compression at ~10–15× and it keeps full Postgres/SQL, continuous aggregates,
  and (critically for this project) *joins to relational identity tables in the same database*. It
  trails pure columnar engines by 3–10× on large `GROUP BY`/aggregation scans.
  https://sanj.dev/post/postgresql-timescaledb-clickhouse-comparison/ ,
  https://oneuptime.com/blog/post/2026-01-21-clickhouse-vs-timescaledb/view (accessed 2026-07-14)
  `[vendor/comparison-blog benchmarks — directional]`.
- **ClickHouse** — the columnar OLAP workhorse. ~10–20× compression, sub-second aggregations on
  billions of rows, ~$25.30/TB/month on ClickHouse Cloud, and 30–60% cheaper than Snowflake/BigQuery
  for scan-heavy analytics. https://clickhouse.com/resources/engineering/best-columnar-databases ,
  https://toolradar.com/tools/clickhouse/pricing (accessed 2026-07-14). Overkill for 138M rows, but
  the right tool if the derived layers (below) fan out to per-day, per-object, per-pair time series.
- **DuckDB + Parquet on object storage** — the actual sweet spot for this workload. Embedded, zero
  standing cluster, reads Parquet directly from S3/R2 via HTTP range requests, prunes files/row-
  groups/columns, and (per SME case studies) cut analytics spend ~80% vs a warehouse; MotherDuck
  reportedly crossed 10k paying teams in Q1 2026.
  https://datasofttechnologies.com/blog/duckdb-is-quietly-replacing-the-sme-analytics-stack-a-2026-reality-check
  (accessed 2026-07-14) `[Medium/vendor-blog figures — unverified specifics]`.
- **Iceberg / lakehouse** — the 2026 archival pattern is *hot/cold tiering*: a fast engine
  (ClickHouse) on recent data, Apache Iceberg on object storage for the cold tail, engine-agnostic
  and ACID. S3 Standard is ~$0.023/GB/month; ClickHouse 25.7–25.9 added full Iceberg read/write.
  https://timexinno.com/the-modern-data-lakehouse-in-2026-apache-iceberg-clickhouse-and-the-hot-cold-tier-pattern/
  (accessed 2026-07-14) `[blog — directional]`.

**Fit to *this* workload** (append-mostly, time-windowed, no OLTP, one operator): the honest answer
is **DuckDB + Parquet/Iceberg on object storage for the fact layer + derived layers, with the
identity graph in Postgres (optionally TimescaleDB) so identity joins stay transactional.** That is
"cheap at scale" in the only sense that matters to a solo builder: near-zero fixed cost, no cluster
to babysit, full-history scans that cost cents, and columnar layout so the change-point/pattern-of-
life queries (§Q2) are fast despite touching the entire archive. ClickHouse is the upgrade path if a
derived layer ever fans out to 10^9–10^10 rows; it is not needed on day one.

### 1.3 What scientific/astronomy data actually faces — the real "TB/night" problem

Astronomy is the honest yardstick, and it dwarfs satellite data. The Vera C. Rubin Observatory
produces **~20 TB of data products *per night* and ~500 PB of images/products over its 10-year
survey**, and — the relevant part — its object catalogs and source tables are generated *natively in
Apache Parquet*, with a Spark connector to read legacy FITS.
https://cloud.google.com/blog/topics/hpc/rubin-science-platform-to-be-hosted-on-google-cloud ,
https://arxiv.org/pdf/2011.06044 (accessed 2026-07-14). The broader field is mid-migration from FITS
(the 1981 flat-file standard) to Parquet/Arrow on cloud object storage precisely because "cheap
queryable time-series/catalog at scale" was genuinely underserved by FITS + local disk.

So the capability *is* real and *is* underserved in science — but note the scale gap: Rubin's
*one-night* 20 TB is ~1,000× this project's *entire multi-decade* compressed archive. The transfer-
able lesson is not "satellite data is big"; it is that **columnar-on-object-storage is the proven,
boring, correct substrate for append-mostly scientific time series**, and adopting it is table
stakes, not a moat.

### 1.4 Verdict: the engine is an enabler, not a moat — but it sharpens the real moat

**Is the engine a credible *additional* moat beyond the data/identity moat? No — not on scale, and
saying otherwise would be dishonest.** 138M rows is not "extreme scale," any competent engineer
reproduces the storage/query layer with off-the-shelf DuckDB or ClickHouse in a weekend, and there
is no proprietary engine IP here. Positioning the *engine* as the moat would invite an easy, correct
rebuttal.

**What it *is*: the cost structure that lets one person operate at catalog scale and continuously
run/backfill the derived layers that *are* the moat.** The defensibility is not "we can store
billions of rows" (everyone can); it is **the co-location of resolved, temporal identity with the
raw element-set time series in one queryable substrate**, so that a question like *"every maneuver
by every object operator X owned, during the window they owned it"* is a single columnar scan + SCD2
join. Orbital Radar and Vantafort (§Q2.1) can scan elsets too — but they have no owner-at-time-T
layer to join to, so they structurally cannot answer that question. The engine matters because it
makes the *join of the two moats* cheap, backfillable, and continuously refreshable by a solo
operator.

**The staff-engineer talking point (use this, not a scale brag):**
> "The archive is only ~10^8 elsets — this was never a big-data problem, and I didn't pretend it
> was. I chose a columnar-on-object-storage fact layer (DuckDB/Parquet, ClickHouse on the upgrade
> path) for one reason: it makes full-history change-point scans across the whole catalog cost
> cents, so I can *backfill* physics-inferred state — maneuvers, status transitions — retroactively
> over 25 years of history and refresh it twice a day for the price of a coffee. The engine isn't
> the differentiator; it's what makes the identity-graph-joined-to-time-series differentiator
> operable by one person. The right boring stack *is* the flex."

That is credible, honest, and reframes "cheap engine" from a (weak) scale claim into a (strong)
*operability + backfill* claim that ties directly to the P0 behavioral-status oracle already in
`docs/PRODUCT.md`.

---

## Q2 — Novel time-series layers, validated and ranked

Rubric per candidate: **what it is · who does it (open/closed/nobody) · 2026 defense/SDA relevance ·
solo-feasibility from public element sets · fit to the cheap-time-series engine.**

### 2.1 Maneuver detection / pattern-of-life

- **What:** change-point detection on element history (semi-major-axis / mean-motion steps, station-
  keeping variance collapse, drag-decay onset) across the whole catalog → a maneuver + behavioral-
  mode ("pattern-of-life") catalog, with estimated delta-V.
- **Who does it — this is NOT unclaimed, and honesty matters here:**
  - *Academia:* a deep literature — GMM/Mahalanobis-distance, CNN on TLE segments for GEO station-
    keeping, LSTM pattern-of-life. https://arc.aiaa.org/doi/10.2514/6.2025-98101 ,
    https://www.sciencedirect.com/science/article/abs/pii/S0273117723008141 (accessed 2026-07-14).
  - *MIT ARCLab* ran the **Prize for AI Innovation in Space (2024)** on GEO pattern-of-life,
    releasing the open **SPLID** dataset (real + synthetic, 2402 trajectories, 2-h resolution) and a
    dev-kit — a public benchmark, and intent to repeat in 2025/26.
    https://news.mit.edu/2024/mit-arclab-announces-winners-inaugural-prize-ai-innovation-space-0711 ,
    https://link.springer.com/article/10.1007/s40295-025-00515-5 ,
    https://github.com/ARCLab-MIT/splid-devkit (accessed 2026-07-14).
  - *Commercial:* COMSPOC, ExoAnalytic, Slingshot detect maneuvers as part of SSA (closed).
  - **Two FREE web tools already publish live maneuver detection across the catalog:**
    **Orbital Radar** (free, community-funded; 14,000+ objects; diffs each new TLE vs predecessor
    against perturbation thresholds; classifies + estimates delta-V via vis-viva; "within hours")
    https://orbitalradar.com/satellite-maneuver-tracker (accessed 2026-07-14); and **Vantafort**
    (free, 31,000+ objects; maneuver + conjunction + reentry; **92.2% precision, 96.3% high-
    confidence, validated against laser-ranging ground truth**; free API endpoints)
    https://vantafort.com/ (accessed 2026-07-14).
- **So what's actually novel?** Raw "detect maneuvers from TLEs" is *solved and free*. The whitespace
  is one level up and is exactly this project's competency: **an open, queryable maneuver/pattern-of-
  life *history* joined to resolved identity — owner-at-time-of-maneuver, per-operator behavioral
  profiles over years, backfilled across the full `gp_history`, with per-event provenance.** No
  search surfaced an open, bulk-downloadable, identity-joined historical maneuver *catalog*; the
  closest open artifacts are a single-operator research dataset (SpaceTrack-TimeSeries, Starlink
  only, https://arxiv.org/html/2506.13034v1 , accessed 2026-07-14) and MIT's simulated benchmark.
  The free tools are *live widgets/feeds*, not queryable owner-attributed histories.
- **2026 SDA/defense relevance:** very high — "pattern-of-life" is the defining SDA phrase of 2025–26
  (MIT prize; the "dogfighting"/inspector narrative below).
- **Solo-feasibility:** high. Pure public GP; algorithms well-documented; sma/element change-points
  need no covariance.
- **Engine-fit:** **highest of all four.** Full-history change-point scan is the canonical columnar
  time-series workload, it is backfillable, and it *is* the query that justifies the §Q1 stack.

### 2.2 RPO / proximity & rendezvous detection ("loitering"/inspector behavior)

- **What:** propagate the catalog to common epochs, screen for objects that *repeatedly* approach
  specific others (co-location, box-keeping, loitering) → an open inspector/RPO-pattern catalog.
- **Who does it:** Secure World Foundation publishes periodic RPO fact sheets (mostly GEO, hand-
  curated) https://swfound.org/publications-and-reports/u-s-military-and-intelligence-rendezvous-and-proximity-operations-fact-sheet
  (accessed 2026-07-14); LeoLabs observes RPO via radar; academia (AMOS) studies SST/SDA RPO
  monitoring. No *open, automated, catalog-wide* loitering-detection product found.
- **2026 defense heat: the hottest of the four.** China's SJ-21/SJ-25 ran repeated <1 km "zero-prox"
  approaches ~7–8× in the first half of January 2026; Space Force flagged five Chinese satellites
  "dogfighting" in 2025; Russian Luch/Cosmos inspectors persist.
  https://breakingdefense.com/2025/03/5-chinese-satellites-practiced-dogfighting-in-space-space-force-says/ ,
  https://isruniversity.com/2026/01/19/issue-137/ (accessed 2026-07-14).
- **Solo-feasibility from public GP *without covariance*: partial — and easy to over-claim.** You can
  compute close approaches in propagated *position* and, more robustly, detect *repeated relative
  geometry* (loitering) — but public TLE accuracy is ~km-scale with no covariance, so true miss-
  distance / probability-of-collision is off the table. GEO co-location/loitering (slow relative
  dynamics) is genuinely feasible; LEO RPO from TLE alone is noisy and prone to false positives.
- **Engine-fit: medium.** Detection is propagation + O(N²) spatial screening (compute-bound), not a
  pure columnar scan; the *output* (proximity-event and loitering-pair time series) stores and
  queries well, but the engine isn't the bottleneck the way it is for §2.1.

### 2.3 Reentry / decay-prediction time series

- **What:** continuously updated decay forecasts for the hundreds of near-decay objects; casualty-
  risk / regulatory framing.
- **Who does it — the most *publicly saturated* of the four.** Space-Track already publishes a public
  **60-day decay prediction** and **TIP (Tracking & Impact Prediction) messages** on a cadence (T-4d
  … T-2h); ESA runs a public reentry-prediction site; Aerospace Corp's CORDS maintains a public
  reentry database. https://www.space-track.org/documentation ,
  https://reentry.esoc.esa.int/home , https://aerospace.org/reentries (accessed 2026-07-14).
- **Novelty: low–medium.** The forecast itself is commoditized *and* government-published; a solo
  builder without high-fidelity drag modeling or covariance would reproduce it *worse*. The only
  defensible open angle is the *time series of forecast evolution* + casualty-risk (EC ≤ 1:10,000
  regulatory threshold, https://www.nature.com/articles/s44453-025-00007-8 , accessed 2026-07-14)
  joined to owner — but "decay onset from element history + owner" is already the P0 behavioral-
  status oracle in `docs/PRODUCT.md`, so this is largely a *framing* of existing P0 work, not a new
  layer.
- **Solo-feasibility:** medium (decay-onset detection yes; competitive impact prediction no).
- **Engine-fit:** medium (forecast-history is time-series-shaped, but the value is physics, not the
  scan).

### 2.4 RF / spectrum crosswalk (SatNOGS ↔ ITU/FCC ↔ physical object)

- **What:** link transmitter/frequency identity and ITU/FCC filing identity to the physical object.
- **Who does it — partly done, and open already.** **SatNOGS DB already carries NORAD IDs *and* ITU
  notification links per object, under CC-BY-SA-4.0** — so the RF↔NORAD↔ITU *link* exists in the
  open. https://db.satnogs.org/ , https://wiki.satnogs.org/Spectrum_Management (accessed 2026-07-14).
  ITU Space Explorer and FCC IBFS remain separate filing systems (per `landscape.md` §3.3).
- **Novelty: medium**, but it is an **identity/graph** problem, not a time-series one — it belongs to
  the identity graph (already **P4** in `docs/PRODUCT.md`), and SatNOGS pre-empts part of its
  novelty.
- **Solo-feasibility:** medium (SatNOGS API + ITU/FCC scraping/entity-resolution).
- **Engine-fit: lowest.** Almost no time-series content; it does not exercise or justify the cheap-
  time-series engine at all.

### 2.5 Ranking (novelty × value × solo-feasibility × engine-fit)

| Rank | Layer | Novelty (as an *open, identity-joined* product) | Defense/SDA value | Solo-feasible from public GP | Engine-fit | Net |
|---|---|---|---|---|---|---|
| **1** | **Maneuver detection / pattern-of-life *catalog*** | High (detection is solved+free; the *identity-joined queryable history* is not) | High | **High** | **Highest** | **Best** |
| 2 | RPO / proximity / loitering | High (open automated loitering catalog is unclaimed) | **Highest** | Partial (no covariance; GEO-loitering ok, LEO noisy) | Medium | Strong-but-risky |
| 3 | Reentry / decay time series | Low–med (gov-published; overlaps existing P0) | Medium | Medium | Medium | Weak |
| 4 | RF / spectrum crosswalk | Medium (SatNOGS already links NORAD↔ITU) | Medium | Medium | **Lowest** | Wrong lane for the engine thesis |

### 2.6 The single best first build

**Build the open, queryable, identity-joined maneuver / pattern-of-life *catalog* — backfilled
across full `gp_history` and refreshed twice daily.** One line why: **it is the only candidate whose
core computation *is* the cheap-time-series-engine workload (full-history change-point scan,
backfillable), it is fully solo-feasible from public element sets, it rides the hottest 2026 SDA
narrative — and its one durable differentiator over the free detectors (Orbital Radar, Vantafort) is
the very thing those tools structurally lack: the SCD2 owner-at-time-of-maneuver join, i.e.
"pattern-of-life *per operator, over years*," which only this project's identity graph can produce.**

It also compounds: a maneuver catalog is the substrate for RPO (§2.2 — maneuvers are the atoms of an
approach) and directly feeds the P0 behavioral-status oracle. Ship maneuver-history first; layer
GEO-loitering detection on top once the maneuver atoms exist.

---

## Sourcing honesty

- **Strongest correction to the brief's framing:** "an open maneuver catalog is novel" is only half
  true. *Live* maneuver detection across the catalog is already **free** from at least two tools —
  **Orbital Radar** and **Vantafort** (the latter citing 92.2% precision vs laser-ranging). What is
  unclaimed is the *identity-joined, queryable, backfilled history* (owner-at-maneuver, per-operator
  pattern-of-life). Pitch that, not "we detect maneuvers." https://orbitalradar.com/satellite-maneuver-tracker ,
  https://vantafort.com/ (accessed 2026-07-14).
- **Second correction:** the workload is ~**138M elsets total**, not billions — this was never big
  data, and the engine is therefore an *enabler/operability* moat, not a *scale* moat. Claiming
  scale would be the easiest thing on this project to rebut. https://www.space-track.org/documentation
  (accessed 2026-07-14).
- **Third:** SatNOGS already links NORAD ↔ ITU in the open, deflating part of the RF-crosswalk
  novelty; treat that layer as an identity-graph (P4) play, not a time-series/engine play.
- **`[unverified]` / low-authority:** DuckDB "80% cheaper" and "MotherDuck 10k paying teams Q1 2026"
  (Medium/vendor blogs); TimescaleDB-vs-ClickHouse ratios (comparison blogs); Iceberg hot/cold cost
  figures (blog). All directional, cited as such, not audited primary benchmarks.
- **Weakest negative (by design):** "no open, identity-joined historical maneuver catalog exists" is
  a could-not-find, not a verified does-not-exist — strong but rebuttable, consistent with
  `landscape.md` §1.9.
