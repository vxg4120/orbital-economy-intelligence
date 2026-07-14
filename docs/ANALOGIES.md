# Explaining It — Analogies & One-Liners

A kit for explaining this project to anyone. Organized by how much the listener already knows about
space, plus a "match their world" set and per-concept analogies you can mix and match. Pick the one
that fits the room; don't recite all of them.

**The golden rule:** lead with the *problem* (nobody agrees who owns a satellite or whether it's
alive), not the *architecture*. People remember problems and pictures, not schemas.

---

## The one perfect line (if you only get one sentence)

> **"It's a credit bureau for satellites — I pull the scattered, conflicting records about 70,000
> orbiting objects into one trustworthy file that says who owns each one, right now, and whether
> it's actually still alive."**

Credit bureau works for almost everyone because everyone understands the shape: your financial
identity is scattered across banks and lenders who don't talk to each other, and a bureau
reconciles it into one file with a history. Swap "banks" for "space catalogs."

Two strong alternates depending on the room:
- **"Carfax for spacecraft"** — an independent history report on an object you trust more than the seller. (Best for lifecycle/ownership/"is it a lemon.")
- **"The LEI for space"** — only for finance-literate listeners (see the finance analogy below); lands hard with them, blank stares from everyone else.

---

## Tier 0 — Someone with zero space knowledge (a friend, a recruiter, your mom)

Everyday-life analogies, no jargon, no orbits.

- **The core problem:** "One satellite has like five different names in five different databases — the way you're a driver's license number, a passport number, a work badge, and 'Bob from the gym' all at once, and no single system knows they're all you. I built the thing that figures out they're all the same object."
- **Merging duplicates:** "You know how your phone asks 'merge these two contacts?' I do that, but for 70,000 things in space — and the sources genuinely disagree, so I have to be a referee, not just a merger."
- **Stale ownership:** "When two companies merge, the space catalog never gets the memo. It still lists satellites under a company that got bought three years ago — like the DMV mailing your parking tickets to whoever owned your car in 2019. I keep track of who *actually* owns each one, and when it changed hands."
- **Is it alive:** "The official record says a satellite is 'active,' but I can look at how it's moving and tell it actually died months ago — like knowing a store is out of business because the lights are off and the lot's empty, even though Google still says 'Open.'"
- **Why it matters:** "There are ten times more satellites up there than a few years ago, and nobody's keeping the paperwork straight. Insurance, collision-avoidance, and 'did this company clean up its dead satellites like it promised' all depend on records that are wrong. I fix the records."
- **The party version (a vivid cold open):** "Here's a weird fact: there are about 70,000 things orbiting Earth, and the official catalogs literally can't agree on who owns them or whether they still work. I built the system that sorts it out."

## Tier 1 — General tech / business / data people (a PM, a data-platform manager, a founder)

They know data concepts. Speak in those.

- **The engineering essence:** "It's master-data management and entity resolution — the classic 'which of these records are the same customer' problem — except the entities are satellites, there's no universal ID, and the sources actively contradict each other. I built the crosswalk with per-attribute provenance and an auditable merge log."
- **The dedup framing:** "Salesforce dedupe, but for 70,000 satellites and with the ownership history modeled as a slowly-changing dimension, so a company merger doesn't corrupt your historical analytics."
- **My own résumé rhyme:** "Same problem I solved at CannMenus — the same product sold under different names at different shops with no universal barcode. I normalized and joined that data; here I'm doing it for low Earth orbit."
- **Conflict-as-product (the clever bit):** "Everyone else hides the disagreement behind one curated answer. I expose it — 'source A says active, source B says decayed, here's the confidence and who I sided with.' It's Rotten Tomatoes instead of a single fake average: I show you where the critics disagree."
- **The market tell:** "The one company that does the identity layer commercially — Seradata — got acquired by Slingshot for it. I built the open, provenance-tracked version as a portfolio piece."
- **The AI-era honest frame:** "AI can generate the pipeline in a weekend now — that's not the moat. The moat is a measured accuracy program, a year of observations you can't backfill, and being the source people cite. So that's where I put the work."

## Tier 2 — Space-curious (reads about Starlink, knows satellites exist, not technical)

They can handle "orbits" and "satellites die." Don't over-explain, don't go full jargon.

- **The reframe:** "Everyone tracks *where* satellites are — that's the collision-avoidance problem, and lots of companies do it. Almost nobody tracks *whose they are and whether they're actually working* — and that record goes stale the moment a company gets acquired or a satellite quietly fails. I built the missing ownership-and-lifecycle layer."
- **The physics hook:** "A satellite's altitude over time basically tells you its life story — you can see it climb into position after launch, hold its slot for years, then one day stop holding and start falling. I read that curve to tell you the truth about what a satellite is doing, even when the catalog is wrong."
- **The megaconstellation angle:** "SpaceX alone is retiring about 40 satellites a month and launching hundreds. The old way of tracking all this — humans typing it into a database by hand — doesn't scale to that. I automate it from the physics."
- **The accountability angle:** "There are new rules that say operators have to deorbit their dead satellites within five years — but nobody actually checks. I can. It's the difference between a company *saying* it recycles and someone *auditing* the dumpster."

## Tier 3 — Industry / expert (an SSA engineer, astrodynamicist, space-data insider)

Precision. No dumbing down. Lead with the differentiation, not the concept.

- **The full description:** "A provenance-tracked, temporally-versioned cross-catalog identity graph over SATCAT and GCAT — SCD2 ownership through M&A, per-attribute source assertions, an auditable merge log, and conflict reporting as a first-class output rather than a flattened curator's answer."
- **The differentiator in one breath:** "It's the open, auditable version of Seradata's identity layer — with the machine-readable disagreement between SATCAT and GCAT that a curated database structurally can't give you."
- **The oracle:** "And a behavioral status oracle: change-point detection on the semi-major-axis time series to infer operational status and death-date from station-keeping collapse — which, unlike catalog status history, is *backfillable* from GP history. Last detected maneuver approximates last day alive."
- **The honest boundary (earns credibility with experts):** "I own no sensor and do no conjunction assessment — it's deliberately the master-data lane, not the sensing or CA lane. It sits *on top of* what LeoLabs or the 18th produce, not in competition with it."

---

## Match the listener's world (profession-keyed)

When you know the person's field, borrow its vocabulary:

- **Finance / fintech:** "It's the LEI for space. Finance had securities with no universal identifier and ownership chains nobody tracked — the 2008 crisis forced the Legal Entity Identifier into existence. Space is pre-LEI, and the megaconstellation era just made that urgent."
- **Lawyer:** "It's title search for orbital assets — establishing a clean chain of who owned what, when. Which matters, because collision liability attaches to the owner *at the time of the event*."
- **Doctor:** "It's differential diagnosis for satellite catalogs — three sources, three answers, and I reconcile them with the evidence trail intact. And the physics oracle is basically reading the EKG instead of waiting for the death certificate."
- **Journalist / analyst:** "It's an independent fact-checker for what satellite operators claim they're doing in orbit — deployment pace, deorbit compliance — verified from public physics, not press releases."
- **ESG / sustainability:** "It's emissions monitoring for space junk. There are disposal rules now but no measurement layer — I verify who actually cleans up their dead satellites versus who just says they do."
- **Supply chain / logistics:** "It's a single source of truth reconciling SKUs across suppliers who all use different part numbers — except the SKUs are satellites and the suppliers are government catalogs."
- **Real estate:** "It's Zillow-vs-Redfin-vs-the-county-assessor all disagreeing on your house — and I'm the layer that shows you the discrepancies with sources instead of one made-up number."
- **Security / fraud:** "It's entity resolution plus anomaly detection — linking scattered identities into one, then flagging when the behavior pattern breaks (a satellite that stops station-keeping is like an account that suddenly acts compromised)."

---

## Per-concept analogies (mix and match)

When you need to explain *one specific piece*, grab the matching picture:

| Concept | Analogy |
|---|---|
| Entity resolution (one object, many IDs) | Merging duplicate phone contacts / a credit bureau assembling one file from many lenders |
| No universal identifier | The same product sold under different names with no shared barcode (CannMenus) |
| Temporal ownership (SCD2) | A property's title chain — who owned it *when*, not just now / the DMV never updating a car's registered owner |
| Catalogs disagree | Rotten Tomatoes showing where critics split, instead of one fake average / Moody's vs S&P on a bond |
| Provenance ("says who?") | A nutrition label that names the supplier of every ingredient / a footnoted article vs an anonymous rumor |
| Merge audit log (no silent merges) | Track changes / git blame — every decision is attributable and reversible |
| Behavioral status oracle | An EKG vs a death certificate / a Fitbit that flatlined / lights-off-but-Google-says-open |
| The auditor thesis | An emissions-testing lab (nobody trusts the car company's own numbers) / a health inspector who shows up |
| The whole product | A credit bureau for satellites / Carfax for spacecraft / a Bloomberg terminal for the orbital economy* |
| Provenance precedence config | House rules for whose word wins on each fact — written down, not buried in code |
| The rollover / BIGINT | Y2K for satellite serial numbers — the counter's running out of digits / phone numbers running out of area codes |
| Politeness ledger (rate limits) | A considerate houseguest who checks before raiding the fridge, so they don't get banned |

\* *"Bloomberg terminal for the orbital economy" describes the ambition, not today's state — use it as a vision line, not a claim, in a technical room.*

---

## Calibration notes (so you don't overclaim)

- **Accurate right now:** credit bureau, Carfax, entity resolution/MDM, conflict-as-Rotten-Tomatoes, title search, the LEI analogy, "reads the physics to tell if it's alive" (the oracle is prototyped, not production — say "I can" carefully, or "I'm building the system that").
- **Directional / aspirational (flag as vision):** "Bloomberg terminal for space," "the auditor of the megaconstellation era." True as a *direction*; don't state them as shipped products to someone who'll probe.
- **Where every analogy breaks (know the seam):** none of these capture that I do *no sensing* — I don't find objects, I reconcile and interpret records about objects other people found. If someone pushes "so you track satellites?", correct gently: "I don't track them — I identify and interpret them. I sit on top of the trackers."
- **The reflex to practice:** problem first, picture second, architecture only if they ask. "Nobody agrees who owns a satellite → it's like a credit bureau → and under the hood it's a provenance-tracked identity graph." Most conversations stop happily at the picture.
