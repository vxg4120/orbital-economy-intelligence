/* Per-stratum arbitration guides. Each distills, for one failure mode: what each source's field
   institutionally MEANS, what to CHECK before deciding, and how the four verdicts map onto the
   question. These are the domain notes a first-time arbitrator would otherwise have to rediscover
   from the SATCAT/GCAT documentation and the identity-graph resolution rules. Displayed in the
   collapsible "Arbitration guide" panel on the case screen, keyed by case_type. */

export interface ReviewGuide {
  /** One-line framing shown next to the disclosure toggle. */
  tldr: string;
  /** 4–8 sentences of institutional context, split into short paragraphs. */
  paragraphs: string[];
}

export const REVIEW_GUIDES: Record<string, ReviewGuide> = {
  status_conflict: {
    tldr: "Is one source stale, or do the two just mean different things by 'status'?",
    paragraphs: [
      "SATCAT's status flag is an operational/administrative code the 18th SDS maintains: '+' active, '-' inactive, 'D' decayed, '?' unknown. For classified or sensitive objects it is frequently policy-frozen at a conservative value — silence is not liveness, and an old '-' can outlive the actual reentry by years.",
      "GCAT's status is Jonathan McDowell's own analytic call, synthesized from amateur tracking, reentry predictions, and launch/press record; it is often the more current view for decayed or defunct objects, and its 'D' reasoning usually cites a specific date.",
      "Check the element-set availability (a truly on-orbit object should have recent GP elements) and the decay-date claims: a ±1-day disagreement on reentry is observation ambiguity, not a real status conflict. Prefer the source whose story the orbit corroborates.",
      "Verdict: correct if the resolved status matches physical reality; partial if it picks the right liveness but the wrong shade (e.g. INACTIVE vs GRAVEYARD); incorrect if the graph reports the stale value; unresolvable if neither source plus the orbit can settle it.",
    ],
  },
  decay_conflict: {
    tldr: "Which reentry date is real — or is the gap just date-format vagueness?",
    paragraphs: [
      "SATCAT decay dates are precise calendar dates tied to the last observation before reentry; they are usually authoritative to the day for tracked objects. GCAT decay dates can be exact OR deliberately vague ('1980 Jan', '1979?'), encoding genuine uncertainty when reentry was inferred rather than observed.",
      "A large day-gap is often an apples-to-oranges artifact: a vague GCAT month compared against a precise SATCAT day parses to the 1st of the month and looks like a 30-day 'conflict' that is really just coarser precision. Read the raw strings, not only the parsed dates.",
      "Check which object it is: for debris and rocket bodies the SATCAT observation is typically the ground truth; for objects that faded from tracking, GCAT's inferred window may be all anyone has.",
      "Verdict: correct if the resolved date is the defensible reentry; partial if it is in the right window but the wrong precision; incorrect if it takes the weaker claim over a well-observed one; unresolvable when both are genuinely uncertain.",
    ],
  },
  owner_dispute: {
    tldr: "SATCAT and GCAT owner codes resolve to two different commercial operators — who really operates it?",
    paragraphs: [
      "SATCAT owner is a country/organization code chosen by the catalog maintainer, often the launching or registering state's designated operator. GCAT owner is McDowell's operator attribution, which tracks commercial reality (the actual company flying the bird) and manufacturer/customer distinctions more closely.",
      "These disagree when the two catalogs pick different links in the same commercial chain — a manufacturer vs. the operating customer, a national agency vs. the company it contracts, or two names for one firm. The selector already excluded pure parent/child hierarchy cases, so this is a genuine two-operators question.",
      "Check the resolved owner against public record: the operator's own fleet list, the launch customer named in press, and any brand rename. The right answer is who commands the satellite today, not who built or registered it.",
      "Verdict: correct if the resolved operator is the true current operator; partial if it names the right corporate family but the wrong entity; incorrect if it picks the wrong operator; unresolvable if the public record cannot disambiguate.",
    ],
  },
  type_conflict: {
    tldr: "SATCAT says debris, GCAT says payload — is it a real spacecraft or a fragment?",
    paragraphs: [
      "SATCAT's object type ('PAY', 'R/B', 'DEB') is a coarse tracking classification; small or secondary payloads, subsatellites, and deployed objects are sometimes carried as DEBRIS because they were catalogued as fragments of a parent. GCAT's SatType is a finer, intent-based taxonomy that distinguishes actual payloads from debris even for tiny objects.",
      "The classic true-positive here is a deployed subsatellite or a cubesat that SATCAT never reclassified from its debris origin — GCAT calls it a payload because it was a designed spacecraft. The classic false alarm is genuine mission-related debris that GCAT happens to name.",
      "Check the GCAT name and any mission notes: a real payload usually has a program/spacecraft name, a mass, and an operator, whereas debris reads as 'DEB', 'PLAT', or a fragment tag. Orbit and launch context (rideshare manifests) help.",
      "Verdict: correct if the resolved object_type matches what the object physically is; partial if the coarse class is right but the nuance is lost; incorrect if the graph took the wrong classification; unresolvable when the sources genuinely cannot tell payload from debris.",
    ],
  },
  stale_owner: {
    tldr: "Post-merger ownership split: is 'child until the deal, parent after' right for THIS satellite?",
    paragraphs: [
      "After an acquisition or merger the resolver models ownership as slowly-changing dimension type-2: the acquired child company owns the satellite up to the deal's close date, and the parent owns it from the close onward. This is deliberately a temporal split, not a rewrite of history.",
      "The question is whether that boundary is correct for this specific bird: some satellites transferred earlier or later than the corporate close, some were carved out of the deal entirely, and some brands persist as an operating subsidiary even after acquisition.",
      "Check the deal's actual close date and whether the satellite was in scope: the parent's fleet disclosures, regulatory transfer filings, and the operator's own naming after the deal. A rename in the catalog is a strong signal the transfer really happened.",
      "Verdict: correct if the child→parent boundary and dates match the real transfer; partial if the parties are right but the date is off; incorrect if this satellite did not transfer as modeled; unresolvable when the transfer terms are not public.",
    ],
  },
  ambiguous_cospar: {
    tldr: "One COSPAR designator maps to several satellites — legit cluster or a bad merge?",
    paragraphs: [
      "An international designator (COSPAR/COSPAR-piece) is assigned per launch and per catalogued piece; multiple distinct objects from one launch legitimately share the launch designator, and near-identical piece letters can collide across catalogs. So more than one satellite under a designator is often entirely correct.",
      "The failure mode to catch is the opposite: two rows that are actually the SAME physical object left un-merged (duplicate identity), or two truly different objects fused under one designator by an over-eager match. Compare the per-object NORAD ids, names, and orbits shown for each satellite in the cluster.",
      "Check whether the objects have separate NORAD numbers and plausibly different names/types (a payload and its rocket body, or two co-passengers) — that supports a legitimate cluster. Identical NORAD/name/orbit across two rows is the red flag for a duplicate that should merge.",
      "Verdict: correct if the multi-mapping reflects genuinely separate objects; partial if some but not all of the cluster is right; incorrect if the graph should have merged (or split) them; unresolvable when the designator data cannot decide.",
    ],
  },
  rideshare_orphan: {
    tldr: "A GCAT-only payload with no NORAD yet — is it a distinct object, and who flies it?",
    paragraphs: [
      "Fresh rideshare payloads often appear in GCAT (from launch manifests and operator announcements) before the US catalog assigns a NORAD number, so 'no SATCAT entry' is normal for a recent launch and is not itself evidence of a phantom object. GCAT's owner code is the operator McDowell recorded from the manifest.",
      "The question has two halves: is this a real, distinct physical spacecraft (not a duplicate of another catalog row or a piece of its deployer), and is the attributed operator right? Constellation deployments make duplicates easy — many near-identical names launched together.",
      "Check the COSPAR piece, the launch date, and the operator attribution against the rideshare manifest and the operator's own list. A distinct name, a distinct piece letter, and a matching operator announcement support 'distinct and correctly attributed'.",
      "Verdict: correct if it is a distinct object with the right operator; partial if the object is real but the operator is unresolved or wrong; incorrect if it duplicates another object or misattributes; unresolvable when the launch is too fresh to confirm.",
    ],
  },
  missed_join_candidate: {
    tldr: "A GCAT object looks name-similar to a same-launch SATCAT object the matcher didn't link — a real missed join?",
    paragraphs: [
      "This is a matcher-recall probe, not an ownership question. The deterministic matcher links objects only on shared NORAD/COSPAR; these candidates were flagged because a GCAT object's normalized name is ≥0.75 similar to a SATCAT object launched within ±30 days that it was NOT linked to. High recall means most of these are genuinely different objects.",
      "Name similarity plus a tight launch window is suggestive but far from proof: rideshares launch dozens of similarly-named payloads together, and sequential names (…-11, …-12) are highly similar yet distinct spacecraft. The matcher being conservative here is usually correct.",
      "Check the identifiers and orbits of the two objects side by side: a true missed join is the SAME physical object under two names (same orbit, compatible piece), whereas co-passengers share a launch but occupy their own slots. Different COSPAR pieces almost always mean different objects.",
      "Verdict: correct (the matcher was right to keep them separate) if they are distinct objects; incorrect if they are truly the same object the matcher missed (a real recall failure); partial for a probable-but-unconfirmed link; unresolvable when the evidence cannot decide.",
    ],
  },
};

export function guideFor(caseType: string): ReviewGuide | null {
  return REVIEW_GUIDES[caseType] ?? null;
}
