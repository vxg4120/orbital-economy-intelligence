# Orbital Behavior Report
### Report #0 — period 2025-07-12 to 2026-07-12 (12 months to latest data)

Generated at: 2026-07-13 20:18:50 UTC

An independent, physics-based audit of megaconstellation-operator behavior. Every figure below is computed live at generation time against the resolved identity graph and the `gp_elements` orbital fact layer; none is supplied by an operator. Behavior is inferred from element-set physics (Space-Track GP) and cross-checked against the reconciled public catalog. Each number is stated with its n and the SQL-derivable definition inline. Method and limitations: see the [methodology annex](#7-methodology--limitations-annex).

## 1. Data basis

The identity graph resolves **69,878** catalogued objects from **613,421** field-level source assertions into **418,086** cross-source identifiers, across **1,435** operators linked by **131** M&A/hierarchy relationships. Behavioral inference draws on **9,700,896** orbital element sets (backfilled Space-Track GP history plus the current CelesTrak GP window), latest epoch **2026-07-12**.

**Data vintage — latest successful ingest per source:**

| source | last_successful_ingest | ok_runs |
| --- | --- | --- |
| celestrak | 2026-07-10 | 6 |
| gcat | 2026-07-10 | 5 |
| spacetrack | 2026-07-10 | 2200 |

**Source assertions by dataset of record:**

| source | assertions |
| --- | --- |
| gcat | 315724 |
| satcat | 297697 |

*Methodology (one paragraph).* Each source (CelesTrak SATCAT, GCAT, Space-Track GP, UCS) is ingested to an append-only assertion ledger with full provenance; a per-attribute precedence resolver (`identity/precedence.yml`) picks a canonical value while every losing assertion stays queryable (disagreements are data, not errors). Orbital behavior is derived purely from published two-line/GP element sets: semi-major axis, perigee and apogee follow from mean motion and eccentricity (Earth mu = 398600.4418 km^3/s^2). No operator telemetry, filing, or press statement is trusted as input. See the annex for the full provenance model and honest limitations.

## 2. Deployment-milestone verification — Amazon Project Kuiper

**FCC 50% deployment milestone: 1,618 satellites operational, due 2026-07-30** (≈18 days after this report's data date).

As of **2026-07-12**, the identity graph attributes **398** Kuiper payloads to Amazon (current SCD2 owner). Their physical state, partitioned so the counts reconcile exactly (deployed = at-shell + raising + deorbited + other):

| state (physics definition) | payloads |
| --- | --- |
| **Confirmed operational** — mean altitude within ±15 km of the 630 km shell AND 30-day rolling SMA stddev < 5 km (station-keeping locked) | 205 |
| **Still orbit-raising** — on-orbit, perigee above 300 km, not yet locked at the shell | 187 |
| **Deorbited** — latest resolved status DECAYED | 4 |
| **Other** — decaying (perigee ≤ 300 km) or not yet tracked | 2 |
| **Total deployed** | 398 |

**What the physics supports as of 2026-07-12:** Kuiper has **205** satellites physically confirmed operational at the ~630 km shell — **12.7%** of the 1,618 required by 2026-07-30. Even counting **every** deployed payload regardless of state (398), the fleet stands at 24.6% of the milestone. Closing the 1,413-satellite gap in the remaining ~18 days would require placing ≈78 operational satellites per day; the observed deployment rate over the last 12 months is ≈28 payloads/month (342 deployed in-period). The milestone will not be met on the current trajectory; this is a physics statement, not a forecast of any FCC waiver.

**Monthly deployment rate (payloads by launch month, current Amazon fleet):**

| launch_month | payloads_deployed |
| --- | --- |
| 2023-10 | 2 |
| 2025-04 | 27 |
| 2025-06 | 27 |
| 2025-07 | 24 |
| 2025-08 | 24 |
| 2025-09 | 27 |
| 2025-10 | 24 |
| 2025-12 | 27 |
| 2026-02 | 32 |
| 2026-04 | 90 |
| 2026-05 | 29 |
| 2026-06 | 36 |
| 2026-07 | 29 |

## 3. Disposal-compliance leaderboard (LEO)

Per benchmark operator. **Lingering (dead-and-high):** payloads whose latest resolved status is INACTIVE, still in LEO (apogee < 2000 km) with perigee above 500 km — dead but not disposed. **Reentries in-period:** payloads resolved DECAYED with a reentry date in [2025-07-12, 2026-07-12], each tracked by physics down to a low final perigee. **Median descent days:** median time from last day at the operational shell (p90 of life mean-altitude, −30 km) to reentry — a short descent from a high shell is the signature of active/propulsive disposal, since passive drag from 500 km takes years.

| operator | inactive_payloads | lingering_dead_and_high | lingering_avg_alt_km | reentries_in_period | median_final_perigee_km | median_op_shell_km | median_descent_days |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Iridium | 25 | 23 | 749.7 | 1 | 148.6 | 440 | 143 |
| Eutelsat | 32 | 3 | 1037.2 | 0 |  |  |  |
| SpaceX | 6 | 2 | 547.7 | 497 | 158.0 | 483 | 80 |
| Planet Labs | 21 | 2 | 573.5 | 38 | 175.6 | 400 | 44 |
| Spire | 4 | 1 | 617.8 | 4 | 157.3 | 452 | 162 |
| Amazon | 1 | 1 | 624.3 | 2 | 184.2 | 453 | 155 |
| ICEYE | 1 | 0 |  | 10 | 153.6 | 404 | 75 |
| Capella Space | 0 | 0 |  | 0 |  |  |  |

*Reading the board.* SpaceX carries a fleet two orders of magnitude larger than any peer yet leaves almost nothing dead-and-high, and executed the overwhelming majority of in-period reentries — its median descent from the operational shell is measured in weeks, far faster than passive drag allows from that altitude, i.e. active disposal. Propulsionless cubesat fleets (Planet Labs, Spire, ICEYE) reenter passively by design from low shells. Iridium concentrates the lingering set: first-generation birds parked high with no disposal dominate the dead-and-high registry below. Absolute counts are not fleet-normalised; read them alongside each operator's fleet size.

**Appendix 3A — dead-and-high registry (32 INACTIVE LEO payloads, perigee > 500 km):**

| operator | norad_id | name | perigee_km | apogee_km | last_tracked |
| --- | --- | --- | --- | --- | --- |
| Eutelsat | 45135 | OneWeb L2-011 | 1220 | 1223 | 2026-07-08 |
| Eutelsat | 45148 | OneWeb L2-014 | 1064 | 1091 | 2026-07-08 |
| Eutelsat | 51645 | OneWeb L13-031 | 799 | 827 | 2026-07-08 |
| Iridium | 25273 | Iridium SV057 | 770 | 773 | 2026-07-08 |
| Iridium | 24793 | Iridium SV007 | 770 | 772 | 2026-07-08 |
| Iridium | 24907 | Iridium SV022 | 769 | 772 | 2026-07-08 |
| Iridium | 25077 | Iridium SV042 | 768 | 771 | 2026-07-08 |
| Iridium | 24944 | Iridium SV029 | 767 | 771 | 2026-07-07 |
| Iridium | 25286 | Iridium SV063 | 767 | 771 | 2026-07-08 |
| Iridium | 24796 | Iridium SV004 | 766 | 769 | 2026-07-08 |
| Iridium | 24903 | Iridium SV026 | 765 | 768 | 2026-07-08 |
| Iridium | 24948 | Iridium SV028 | 765 | 767 | 2026-07-08 |
| Iridium | 24967 | Iridium SV036 | 764 | 767 | 2026-07-08 |
| Iridium | 25043 | Iridium SV038 | 763 | 764 | 2026-07-08 |
| Iridium | 24870 | Iridium SV017 | 763 | 766 | 2026-07-08 |
| Iridium | 24841 | Iridium SV016 | 761 | 762 | 2026-07-08 |
| Iridium | 25078 | Iridium SV044 | 755 | 758 | 2026-07-08 |
| Iridium | 25320 | Iridium SV071 | 753 | 754 | 2026-07-08 |
| Iridium | 24836 | Iridium SV014 | 752 | 757 | 2026-07-08 |
| Iridium | 25319 | Iridium SV069 | 752 | 756 | 2026-07-08 |
| Iridium | 24871 | Iridium SV020 | 745 | 759 | 2026-07-08 |
| Iridium | 25105 | Iridium SV024 | 743 | 757 | 2026-07-08 |
| Iridium | 24842 | Iridium SV011 | 723 | 740 | 2026-07-08 |
| Iridium | 25344 | Iridium SV073 | 716 | 720 | 2026-07-08 |
| Iridium | 25042 | Iridium SV039 | 712 | 740 | 2026-07-08 |
| Amazon | 65776 | Kuiper KA03-15 | 624 | 625 | 2026-07-08 |
| Spire | 40932 | Lemur FM02 | 610 | 626 | 2024-01-06 |
| SpaceX | 33393 | Mass simulator | 569 | 585 | 2026-07-06 |
| Planet Labs | 39429 | Dove 0711 | 562 | 720 | 2026-07-08 |
| SpaceX | 45368 | Starlink V1.0-L5-20 | 518 | 519 | 2026-07-07 |
| Planet Labs | 66731 | Dove 253e | 504 | 508 | 2026-07-08 |
| Iridium | 24795 | Iridium SV005 | 502 | 641 | 2026-07-08 |

## 4. GEO end-of-life conduct

Graveyard-boost compliance among INACTIVE GEO payloads (mean altitude 34000–38000 km). Compliant = perigee raised at least 235 km above the 35786 km belt (IADC minimum re-orbit). Only operators with n ≥ 3 INACTIVE GEO payloads in the graph are listed.

| op | inactive_geo | graveyard_compliant | abandoned_in_belt | median_perigee_above_geo_km |
| --- | --- | --- | --- | --- |
| Eutelsat | 28 | 26 | 1 | 397 |

**Eutelsat** is the only operator with a graveyard-scale INACTIVE GEO cohort in the reconciled graph: **26 of 28** (92.9%) properly boosted to a median **+397 km** above GEO. The exceptions:

| operator | norad_id | name | perigee_vs_geo_km | apogee_vs_geo_km |
| --- | --- | --- | --- | --- |
| Eutelsat | 24931 | Eutelsat HB3 | -714 | -501 |
| Eutelsat | 27554 | Eutelsat W5 | -51 | 61 |

**The catalog cannot audit the rest.** No other operator carries a single INACTIVE GEO payload in the reconciled record — yet **11** payloads physically sit ≥150 km above the belt (graveyarded by the physics) while the catalog still labels them anything but INACTIVE:

| catalog_status | payloads |
| --- | --- |
| ACTIVE | 8 |
| PARTIAL | 2 |
| SPARE | 1 |

That gap is the finding: snapshot status fields miss GEO retirements, so graveyard compliance is only auditable where a source happens to have flagged the object dead. A physics-based end-of-life detector (the behavioral status oracle; see annex) is required to audit the rest involuntarily.

## 5. Accountability integrity

**Stale owner-of-record after M&A.** Objects whose latest SATCAT owner code still resolves to a company that has since been acquired or merged — the public catalog still names the dissolved child. Counts are on-orbit objects per acquisition:

| acquirer | catalog_still_names | acquired_on | attributed_objects | on_orbit |
| --- | --- | --- | --- | --- |
| SES | Intelsat | 2025-07-17 | 139 | 138 |
| Viasat | Inmarsat | 2023-05-30 | 20 | 20 |

**158** on-orbit objects carry an owner-of-record that no longer exists as an independent entity. Note this catches only acquisitions whose SATCAT code maps to a company alias; the ex-OneWeb fleet is coded to the country code 'UK', which maps to no operator at all and is therefore invisible to naive owner-code attribution — the exact failure the next number quantifies.

**Temporal vs. naive attribution (the identity-graph delta), Eutelsat / ex-OneWeb.** SATCAT's OWNER field is a country/agency code, not a company; temporal SCD2 identity resolution assigns each satellite-day to the operator that actually held it.

- Temporal (SCD2) attribution: **708** satellites, **260,809** elset-days.
- Naive SATCAT owner code: **57** satellites, **19,138** elset-days.
- Delta: **651** satellites / **241,671** elset-days — **13.6×** more behavior correctly attributed under temporal resolution.

**Death-certificate disputes.** **4,240** objects have cross-source disagreement on their reentry (decay) date after loose-date normalisation — the public record cannot agree on when these objects died. Five largest disagreements:

| norad_id | object | conflicting_reentry_claims | disagreement_days |
| --- | --- | --- | --- |
| 879 | OGO A | gcat: 1964 Sep  6 0411?; satcat: 2020-08-29 | 20446 |
| 3138 | OGO E | gcat: 1968 Mar  5 1637?; satcat: 2011-07-02 | 15824 |
| 3145 | Agena D 6503 | gcat: 1968 Mar  5 1637?; satcat: 2011-01-18 | 15659 |
| 13367 | Landsat D | gcat: 1983 Jan; satcat: 2025-10-08 | 15621 |
| 5189 | deb Kosmos-374  [*] | gcat: 1982 Nov 10; satcat: 2024-08-14 | 15253 |

## 6. Catalog integrity (the reliability baseline)

How reliable is the public record, per attribute? For every object asserted by two independent sources, the substantive conflict rate (both sides concrete):

| attribute | conflicts | objects with 2 concrete sources | conflict rate | agreement |
| --- | --- | --- | --- | --- |
| Reentry (decay) date | 4,240 | 35,277 | 12.0% | 88.0% |
| Object type | 1,483 | 69,510 | 2.1% | 97.9% |
| Operational status | 35 | 34,479 | 0.1% | 99.9% |

Decay dates are the least reliable field in the public catalog: when two sources both record a reentry date they disagree **12.0%** of the time — roughly one object in eight. Object type and operational status agree far more often, but status agreement is measured only over the 34,479 objects where both sources assert a concrete (non-UNKNOWN) status — most objects carry a concrete status from at most one source, which is itself the coverage limitation noted in the annex.

## 7. Methodology & limitations annex

**Provenance model.** Every value in this report traces to a source assertion. Ingestion writes an append-only `source_assertion` ledger (attribute, value, source, observed-at, ingest run) that never overwrites; a per-attribute precedence resolver (`identity/precedence.yml`) selects the canonical value for each dimension while every losing assertion stays queryable. Object merges are never silent — each is written to `merge_log` with the rule that fired:

| rule_fired | merges |
| --- | --- |
| norad_exact | 416760 |
| cospar_exact | 1326 |

**Trust program (gold-standard evaluation).** A stratified set of **246** hard identity-resolution cases across 7 strata, each with a full evidence packet; **118** carry an AI-researched dossier with cited sources. Human-adjudicated verdicts so far: **0** (correct 0, partial 0, incorrect 0). Resolution accuracy is measured, not assumed; at 0 adjudicated the accuracy rate is not yet reportable and is stated here as pending rather than estimated.

**Limitations (stated plainly — the report's credibility is the product).**

1. **Status is snapshot-based, not a transition ledger.** Operational status is the latest resolved snapshot per object; a genuine append-only status-transition time series is accruing but not yet deep. INACTIVE/GRAVEYARD flags therefore depend on a source having marked the object — which §4 shows is inconsistent for GEO retirements.
2. **The behavioral status oracle is pending.** Physics-inferred operational/dead transitions (station-keeping collapse, drag-decay onset, maneuver change-points) are scaffolded in `analysis/` but not yet in production. Where this report infers behavior (disposal mode in §3, graveyarding in §4) it does so from altitude, descent rate and known propulsion class, and says so; a rigorous per-object disposal-mode classifier awaits the oracle.
3. **Classified and untracked objects are unobservable.** The audit sees only publicly catalogued objects with published element sets; maneuvering or classified assets that withhold GP data cannot be verified here.
4. **Single-source physics.** Orbital history is Space-Track GP (and CelesTrak GP for the current window) only; there is no independent radar/optical cross-check of the element sets themselves. The identity and conflict layers are multi-source; the physics is not.
5. **Attribution coverage.** Behavioral figures are reported only for objects with resolved current ownership and landed GP history; operators without backfilled history (or with owner codes that map to no operator) are under-counted, not zero — the naive-attribution delta in §5 quantifies one such gap.

---
*Orbital Behavior Report #0 · period 2025-07-12 → 2026-07-12 · generated from a read-only query against the resolved identity graph. Re-running on the same data reproduces this document byte-for-byte except the generated-at line.*
