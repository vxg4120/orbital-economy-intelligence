# The Behavioral Status Oracle — Research Framing

**Status: P0 research scaffold.** This document frames the problem, grounds the signal
taxonomy in real satellites pulled from the live OEI database (2025-07 → 2026-07),
sketches candidate algorithms with honest tradeoffs, proposes an evaluation design against
existing labels, names the blind spots, and closes with open questions for the detector
designer. It deliberately stops short of the algorithm — the numbers and the figures in
`analysis/figs/` are the starting material, not the answer.

Companion script: `analysis/case_studies.py` renders every satellite named below.

---

## 1. The problem, stated commercially

Public catalogs answer the wrong question. Space-Track, SATCAT, and the operator feeds were
built to keep two lumps of aluminium from hitting each other — they are *collision-avoidance
physics*, not *commerce, law, or compliance*. The status field, where it exists, is a stale
human opinion: an object is "ACTIVE" until somebody remembers to change it, and nobody is
paid to remember. Yet three of our commercial anchors turn on exactly that field:

- **In-orbit insurance (anchor 1).** A claim hinges on *when the satellite actually died* —
  not when the operator filed, not when the catalog noticed. Premiums are mispriced and
  claims are contested for want of a defensible death date. Physics supplies one.
- **Conjunction triage (anchor 3).** When a close approach is flagged, the operator needs to
  know *is this thing maneuverable* in the next hours. A satellite whose station-keeping
  collapsed three weeks ago is a different risk object than the catalog's "ACTIVE" label
  claims — it will not dodge.
- **Deorbit compliance (anchors 5, 8).** The FCC 5-year rule and ESG capital both need the
  *last-operational date* and the disposal trajectory. "Dead-and-high" is a fineable state.

The decisive property: **physics-inferred status is backfillable, catalog status is not.**
We cannot recover the true status history of an object from a source that only ever stored
its current opinion. But the element sets are a physical record — `gp_history` already holds
the motion, and the motion *is* the status. Run the detector over history once and you have a
transition ledger that could not otherwise exist. That asymmetry is why this is P0.

How big is the gap right now? In the current window, **865 objects are tagged `ACTIVE` with a
null `decay_date` while already in sustained, post-plateau orbital decay** — the "walking
dead." Each is a mispriced insurance line, a mis-triaged conjunction, or a compliance clock
the catalog has not started. That population is the product.

---

## 2. Signal taxonomy, with real satellites and real numbers

Semi-major axis (`sma`, derived from mean motion) is the workhorse channel; perigee is the
more *sensitive* death channel (it craters faster than sma once drag takes over). All figures
below are from `sat_daily` over 2025-07 → 2026-07 unless noted.

### (a) Active station-keeping — low variance, held to a shell
The signature is a *ruler-flat* sma with a tight, symmetric micro-band around a set point.
Propulsion plus a control loop defeats drag; the object does not drift.
- **Iridium NEXT SV119 (NORAD 42959):** sma held at **7155.8 km to ±2.5 m (1σ) over a full
  year**, total range 22 m. This is the tightest control in the fleet — the "ruler."
  (`figs/01_station-keeper_42959.png`.)
- **EUTELSAT 8 WEST B (40875):** GEO, sma 42164.8 km, held to **~0.12 km** — a wider dead-band
  than LEO (GEO station-keeping trades sma tolerance for longitude/inclination control) but
  unmistakably controlled. (`figs/02_...`.)
- **Starlink Group 2-4-22 (55290):** LEO operational baseline, sma 6950.2 km, **1σ ~17 m**.
  This is the "alive" reference every departure is measured against. (`figs/03_...`.)

### (b) Passive-healthy — no propulsion, smooth drag decay (Planet)
A satellite that never station-keeps traces a clean, monotone, *decaying-exponential* glide:
the slope steepens as it sinks into denser air. Healthy, just un-propelled.
- **Planet Dove 251c (60483):** ~**−0.65 km/day**, sma 6858 → 6550 km, reentry **2026-04-25**.
- **Planet Dove 250f (62643):** sma 6881 → 6584 km, reentry **2026-06-19**.
  (`figs/04_...`, `figs/05_...`.) The teaching point: a smooth monotone decline is *not*
  itself a death event — for these objects it is the entire, healthy operational life.

### (c) Death signature — station-keeping collapse → decay onset (**the money example**)
A flat plateau that *breaks* into monotone decay and **does not recover**. The change-point is
the operational death; the reentry is months later.
- **Starlink-3893 / Group 4-13-44 (52579):** ruler-flat at **6917.9 km for ten months**, then a
  clean break **~May 2026** into monotone decay, perigee **538 → 396 km** and still falling.
  Catalog status: **ACTIVE**; `decay_date`: **null**. Physics knew first.
  (`figs/10_death-in-progress_52579.png`.)

This is the whole thesis in one chart, and it comes with a warning (see §3): the naïve
"variance jumped between the two window halves" test that first surfaces it *also* surfaces
controlled maneuvers. The genuine death filter needs the *non-recovery* clause.

### (d) Controlled deorbit — commanded rapid descent (SpaceX)
Plateau, then a deliberate, steady walk-down to reentry — shallower and more regular than an
uncontrolled tumble.
- **Starlink Group 2-1-01 (49131):** 6950 → 6528 km, reentry **2026-05-20**. GCAT marks
  `TOp = 2026-02-17` (a plausible end-of-ops), `TDate = "2026 May 5?"` (provisional).
- **Starlink TSP2-02 (48880):** reentry **2026-06-14**; GCAT `TDate = 2026 May 30`.
- **Starlink Group 2-10-45 (56811):** 6950 → 6536 km, reentry **2026-05-26**.
  (`figs/06_`, `figs/07_`, `figs/08_`.) Note the **~3-month gap between end-of-ops and reentry**
  — the compliance and insurance clocks run on the former, which no catalog field cleanly gives.

### (e) Orbit-raise — birth, not death (must NOT read as anomaly)
A *rising* sma from a low insertion to the operational shell. A naïve variance or slope
detector will scream; it is the opposite of a problem.
- **Kuiper KA03-16 (65777):** climbs **+364 km**, sma 6648 → 7013 km, from a September-2025
  insertion up to its shell. (`figs/09_orbit-raise_65777.png`.) Any detector must whitelist
  sustained *positive* sma trends and the early-life window.

---

## 3. Candidate algorithms — sketches and honest tradeoffs

No implementation here; these are the standard tools, named, with where each breaks.

**Rolling-variance change-point (CUSUM / Bayesian online change-point).** Track a rolling
30-day stddev (or IQR) of `sma_avg` — SPEC §7 metric 1 is already this — and fire when it
jumps. **CUSUM** is cheap, online, and one-sided (good for "variance grew"); **Bayesian Online
Change-Point Detection (BOCPD, Adams & MacKay 2007)** gives a posterior over the change time,
which is exactly the *death-date with uncertainty* the insurance use-case wants.
*The trap, demonstrated:* the "variance between window halves jumped >10×" version of this
test — the one that first finds the money example — is dominated by **controlled maneuvers,
not deaths**. Re-running it surfaced *Starlink Group 5-1-39 (54858)*, which drops ~76 km in
March 2026 and then **re-plateaus at 6861 km and holds** (a commanded shell change), and
*Iridium 43928*, which *raised* ~93 km to a new set point. Both throw an identical variance
spike to a real death. The fix is a **non-recovery / persistence clause**: a death requires the
post-break segment to keep a sustained negative slope (perigee especially), never re-flattening.
See `figs/11_maneuver-not-death_54858.png` beside `figs/10_...` — same spike, opposite meaning.

**Piecewise-linear segmentation of sma(t).** Fit sma as connected line segments and read the
breakpoints and slopes directly (**PELT**, Killick et al. 2012; **bottom-up / SWAB**; or an
L1-trend-filter). This maps cleanly onto the taxonomy: flat segment (slope ≈ 0) = kept;
positive slope = orbit-raise; a slope change from ≈0 to strongly negative = death onset;
piecewise-negative accelerating = decay. It is interpretable and gives both the *date* and a
*rate* to feed reentry prediction. Cost: choosing the penalty/segment count, and robustness to
the occasional bad element set.

**Sawtooth (re-boost) detection via peak-finding on dsma/dt.** Drag make-up shows as a
sawtooth: slow decline, sharp re-boost, repeat. Detecting the re-boost cadence would directly
measure "is it still being flown." **The surprise (see §5):** for well-kept LEO the sawtooth
amplitude is **sub-100 m** — for Iridium 42959 it is a **~±10 m dead-band** — and the daily
continuous aggregate *averages it flat*. It is genuinely present in per-epoch `gp_elements`
(`figs/12_reboost-sawtooth_42959.png`, detrended, meter scale) but invisible in `sat_daily`.
So `scipy.signal.find_peaks` on `d(sma)/dt` is viable **only on per-epoch data with detrending**,
not on the cagg. That is a data-resolution decision to make before this method is worth building.

**Two-state (or three-state) HMM.** Frame operational vs decaying as hidden states with an
absorbing DECAYED state, emissions = (sma slope, rolling variance, perigee slope). An HMM
handles noisy element sets gracefully, gives smoothed state posteriors, and the operational →
decaying transition time is the death estimate. Add a third "maneuvering" state to absorb the
§3-trap false positives. Cost: needs enough labeled arcs to fit transition/emission
parameters, and the "maneuvering" and "dying" states look alike until the non-recovery clause.

A pragmatic composite is likely: piecewise segmentation for interpretable dates + a persistence
rule for non-recovery + an orbit-raise/positive-slope whitelist. But that is the owner's call.

---

## 4. Evaluation design

**Ground truth already in the database.**
- **Reentry dates (the strong label).** `satellite.decay_date` from Space-Track DECAY:
  **570 objects have both a decay_date in the window and gp coverage of the descent**, of which
  **512 are SpaceX physics-confirmed deorbits** — a large, clean, physics-anchored test set for
  the *reentry-date* target and for dead-vs-alive classification.
- **GCAT labels (`raw_gcat_psatcat.extra`, CC-BY 4.0, cite McDowell).** Availability checked:
  of 55,764 psatcat rows, **`TDate` (decay) is real for 23,151** and **`TOp` for 23,481**;
  `TLast` is populated for all but noisily. Map jcat → NORAD via `jcat = 'S' || lpad(norad,5,'0')`.
  `TDate` becomes a corroborating decay label and `TOp` a *candidate end-of-operations* label.
  **Leakage/quality warning — verified, do not skip:** GCAT dates are inconsistent across object
  classes. For deorbiting Starlinks `TDate` is provisional and *early* (49131: `TDate "2026 May 5?"`
  vs real reentry 2026-05-20; flagged `?`). Worse, for *Dove 251c (60483)* the fields invert —
  `TLast` = the launch date, `TOp` = the decay date. So **`TDate` may be used as a soft,
  ~days-to-weeks-early decay label with an uncertainty flag; `TOp`/`TLast` must be
  class-checked before use, never trusted blind.** The authoritative decay label is
  `satellite.decay_date`.

**The hard truth about the most valuable label.** The commercial target is not the reentry date
(a label exists) — it is the **end-of-operations / station-keeping-collapse date**, and **no
catalog field cleanly provides it.** GCAT `TOp` is the closest proxy and it is unreliable. So
the death-date target must be evaluated on a **small hand-labeled validation set** (annotate the
visible change-point on ~50–100 arcs like `52579`), or bootstrapped from the physics itself and
then spot-audited. Design for this scarcity from day one.

**Metrics.**
- *Death-date MAE (days)* against hand-labeled change-points; report median and 90th percentile,
  not just mean (the tail is where claims are contested).
- *Reentry-date MAE (days)* against `decay_date` for the 570-object set.
- *Dead-vs-alive precision/recall at a query date T* — the conjunction-triage framing: at date T,
  did the oracle correctly call each object maneuverable or not? Sweep T.
- *False-positive rate on maneuvers* — a dedicated slice of known controlled moves and orbit-raises
  (e.g. 54858, 65777, Iridium 43928) that the detector must **not** call dead.

**Leakage warnings.** (1) Do not let features peek past T when scoring status-at-T. (2) The
512 SpaceX deorbits are one operator's disposal doctrine — a low-FP score there does *not*
generalize to GEO graveyarding or to failures. Stratify. (3) `decay_date` can be back-dated by
the source after the fact; freeze labels to a snapshot. (4) The variance-jump heuristic's
apparent success on the money example is partly the *maneuver* population leaking in — score
deaths and maneuvers separately.

---

## 5. Named blind spots

- **Classified / elements-withheld objects.** Many defense payloads have no public GP or
  deliberately degraded elements; the oracle is blind to them by construction. State it.
- **GEO station-keeping differs fundamentally.** GEO holds *longitude*, and lets sma breathe
  ~±1–2 km; end-of-life is a **super-synchronous graveyard boost (sma up ~200–300 km)**, which
  looks like an orbit-raise, not a decay. A LEO-tuned decay detector will miss GEO deaths
  entirely — GEO needs its own signature (the disposal *raise*).
- **Data gaps at window edges.** Real coverage is dense only **2025-07 → 2026-07**; pre-2025-07
  is a handful of objects (a `gp_history` backfill limitation, not physics). A "plateau then
  silence" at the right edge is ambiguous — death vs simply the end of ingest. Censor the edges.
- **The daily-cagg resolution floor.** Sub-100 m station-keeping structure (the re-boost
  sawtooth) is averaged away in `sat_daily`; anything relying on it needs per-epoch `gp_elements`.
- **Non-Starlink disposal doctrines are under-sampled.** Almost every full-arc controlled deorbit
  in the window is SpaceX; OneWeb/Planet/others will look different.

---

## 6. Open questions for the detector designer (foundational → advanced)

1. **Channel choice.** sma, perigee, mean motion, or a fusion? Perigee dies faster — is it the
   better primary, with sma for maneuver context?
2. **Resolution.** Build on the `sat_daily` cagg (cheap, but sawtooth-blind) or on per-epoch
   `gp_elements` (richer, heavier)? This gates whether re-boost detection is even on the table.
3. **The death vs maneuver boundary.** What persistence/non-recovery rule cleanly separates
   `52579` (dies) from `54858` (re-plateaus) with minimal latency? How many days of sustained
   negative slope before you commit to "dead"?
4. **Latency vs confidence.** Insurance wants a defensible date (favor confidence); triage wants
   an early warning (favor latency). One model with two operating points, or two models?
5. **GEO.** A separate detector keyed on the graveyard-raise signature, or a unified model with
   a regime feature (sma band / inclination)?
6. **Uncertainty.** Should every death date ship with a posterior interval (BOCPD-style) so the
   insurance product can price the ambiguity rather than hide it?
7. **Orbit-raise immunity.** Whitelist by positive slope, by early-life age window, by launch
   proximity — or learn it? How to avoid flagging a re-boost-heavy shell change as death?
8. **Label bootstrapping.** How to grow the end-of-ops labeled set beyond hand-annotation — can
   the 512 reentries anchor a weakly-supervised model for the (unlabeled) end-of-ops date?
9. **Backfill validation.** Once run over `gp_history`, how do we prove the *historical*
   transitions are right when we have no historical ground truth to check them against?
10. **Productization.** What confidence threshold and refresh cadence turns the raw detector into
    the "walking dead" leaderboard and the twice-daily transition ledger (P1) a buyer would pay for?
