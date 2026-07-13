# Product Thesis — Orbital Economy Intelligence

**The one-liner:** The space economy runs on catalogs built for collision-avoidance physics, not
for commerce, law, or compliance. Every commercially expensive question — who owns this object
*now*, when did it *actually* die, is the operator compliant, who do I call — is a question of
identity, ownership-in-time, and lifecycle. No public source maintains those. Finance forced the
LEI into existence for exactly this class of problem; **space has no LEI.** This project is the
resolved-identity layer with provenance, and everything we build should either deepen that layer
or monetizably surface it.

## Use case → capability map

| # | Use case (buyer) | Money at stake | Capability that serves it | Status |
|---|---|---|---|---|
| 1 | In-orbit insurance underwriting/claims (underwriters) | mispriced premiums, contested claims | temporal ownership + physics-dated death | graph ✅ · physics oracle ⬜ P0 |
| 2 | Sanctions/export screening (compliance teams) | regulatory fines | entity resolution + M&A chains + stale-owner detection | ✅ (surface as lookup/report ⬜) |
| 3 | Conjunction response triage (operators, SSA firms) | collision = total loss | current owner + is-it-maneuverable | owner ✅ · behavioral status ⬜ P0 |
| 4 | Collision-liability attribution (gov legal, insurers) | 9-figure loss allocation | SCD2 owner-at-time-T | ✅ |
| 5 | Deorbit-compliance monitoring (regulators, operators, ESG) | licenses, fines, capital access | status transitions + disposal physics | ledger accruing ⏳ · oracle ⬜ P0 |
| 6 | Constellation competitive intel (strategy/BD, analysts) | capex & positioning | benchmark metrics, deployment/congestion | ✅ (FE panels ⬜ P2) |
| 7 | Spectrum/slot coordination (regulators, operators) | slots worth $100M+ | ITU/FCC ↔ catalog crosswalk | ⬜ P4 |
| 8 | Debris attribution & remediation (ADR cos, policy) | cleanup liability | fragment→parent→owner-at-breakup | partial (graph ✅) |
| 9 | M&A / investment diligence (PE/VC, corp-dev) | fleet valuations | verified fleet inventory + health + liability overhang | ✅ (report product ⬜) |
| 10 | Agent-native catalog truth (AI apps, press, academia) | API/subscription + distribution | MCP server over the graph | ⬜ P3 |

## Build priorities (each item names its commercial anchor)

- **P0 — Behavioral status oracle** (serves 1, 3, 5, 8): infer operational status and death-date
  from element-set physics (station-keeping variance collapse, drag-decay onset, maneuver
  change-points). Uniquely valuable because catalog status fields are stale opinions and — unlike
  catalog history — *physics-inferred transitions can be backfilled* from gp_history. Evaluation
  labels already exist: 500+ physics-confirmed deorbits. Research scaffold: `analysis/`.
- **P1 — Transition ledger in production** (serves 1, 5): the twice-daily scheduled ingest
  (`make schedule-install`) turning status into an append-only time series. Value compounds with
  calendar time; cannot be recreated retroactively. Also catches the 69999→100000 rollover.
- **P2 — Commercial demo surfaces** (serves 5, 6): compliance leaderboard ("dead-and-high" per
  operator), Kuiper FCC-milestone tracker, reentry watch — the pages a buyer/analyst would
  actually reload.
- **P3 — MCP server** (serves 10): read-only tools (resolve_satellite, operator_fleet,
  conflicts_for_object, compliance_score). Zero competitors verified as of 2026-07; cheap;
  rides agent-era distribution.
- **P4 — ITU/FCC filing crosswalk** (serves 7): highest novelty, highest effort; both sides
  public; nobody has joined them.

## Trust program (cross-cutting; what makes any of this sellable)

Gold-standard evaluation: 246 stratified hard cases, AI-researched dossiers with cited sources,
**human-adjudicated verdicts** (docs/gold/verdicts.jsonl), scored error rates
(docs/reports/gold_eval.md). The sentence this buys: *"every number traces to a source assertion,
and resolution accuracy is measured, not assumed."* No open competitor can say it; most closed
ones don't either.

## Positioning by audience

- **Employers (SSA/data companies):** "I independently built and operate the open version of the
  asset you acquired/sell — including the measurement of its accuracy."
- **Customers (insurers, compliance, operators):** start with one narrow, recurring artifact
  (compliance leaderboard or lifecycle feed pilot) — not a platform sale.
- **Acquirers/partners:** the identity graph + accumulated transition history + trust program is
  the moat; the terminal is the demo.
