# The Ledger: visual identity for the Orbital Economy Terminal

Approved 2026-08-06 (direction: observatory ledger → dark plate → Fraunces voice).

## Concept

The site stops imitating mission control and becomes what it is: the published record of the
orbital economy. Every element derives from instruments and ledgers — fine rules, plate
captions, engraved charts, a colophon — with Fraunces as a display voice that gives the
record a byline. Two voices with strict jobs: **Fraunces narrates, mono testifies.**

## Type

- **Fraunces Variable** (SIL OFL, self-hosted via @fontsource-variable/fraunces, bundled by
  Vite — no CDN), used for: topbar brand, view titles, panel titles, hero stat values,
  landing headline, prose surfaces. WONK on only at display sizes (>= ~27px); floor 15px.
- **Mono (existing system stack)** for every datum, table, label, chip, micro-caption.
  Nothing about data presentation changes.

## Palette (evolution, not revolution)

Dark stays. Temperature shifts from blue-cast SaaS-dark to neutral engraved plate:

- Surfaces: neutral ink-blacks (drop the blue cast).
- Hairlines: silver-neutral.
- Ink: warms slightly toward starlight-on-paper; WCAG AA preserved (verified by script).
- Signal blue: survives but desaturates toward instrument glow.
- Status scale, storm amber, error red: untouched (semantic).
- Sequential ramp: retuned to silvery-blue, ordering preserved.
- Plate grain: near-subliminal SVG noise on the void plane, inline data URI, self-contained.

## Signature motifs

1. **Receipt underline** — headline numbers that reconcile against a receipt set carry a fine
   dotted underline and a tooltip stating the reconciliation (count · endpoint · methodology
   version), linking to the receipts URL. The trust machinery becomes visible typography.
2. **Plate captions** — panels are numbered automatically per view ("PLATE 04") via CSS
   counters; quiet, micro-mono, engraved.
3. **Colophon** — every view ends with a one-line edge-note: methodology version, last
   refresh, sources, correction channel. Replaces generic footer.
4. **Ledger rules** — double hairline under section headers; panels hang from rules rather
   than sitting in heavy boxes.
5. **Engraved charts** — hairline axes, outside ticks, no grid flood (environment strip +
   congestion heatmap this pass).

## Scope: first pass

theme.css token evolution · vendored Fraunces · ledger rules + plate captions on all seven
views · Fraunces on display surfaces · receipt underlines on Buses leaderboard + detail ·
colophon bar · chart retouch (env strip, heatmap) · landing headline.

**Out (later passes):** light "print plate" mode, logo, per-view illustration, receipts
drawer UI.

## Proof

Before/after screenshots per view; WCAG AA contrast verification for every changed ink
token; web build green; zero data-layer changes (test suite untouched by construction).
