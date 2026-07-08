/* Single source of truth for the three conflict classes, shared by the Overview
   deep-link cards and the Conflicts tabs so the two views can never disagree on a
   tab slug. `key` is the ?tab= slug (and the ConflictTab union); `statsKey` maps
   to the matching field on Stats["conflicts"] — tsc flags any drift in either. */

import type { Stats } from "../api/types";

export type ConflictTab = "status" | "decay" | "stale";

export interface ConflictTabMeta {
  key: ConflictTab;
  statsKey: keyof Stats["conflicts"];
  tabLabel: string; // short label for the Conflicts tab button
  cardLabel: string; // longer label for the Overview deep-link card
  cardSub: string; // Overview card subtitle
  headline: string; // descriptive paragraph atop the Conflicts table
}

export const CONFLICT_TABS: ConflictTabMeta[] = [
  {
    key: "status",
    statsKey: "status",
    tabLabel: "Status",
    cardLabel: "Status disagreements",
    cardSub: "SATCAT vs GCAT canonical state",
    headline:
      "Objects where SATCAT and GCAT map to different canonical states — one source calls it active, the other reentered. Resolved by the deterministic ordering (observed_at, ingest_run, source_key).",
  },
  {
    key: "decay",
    statsKey: "decay",
    tabLabel: "Decay dates",
    cardLabel: "Decay-date conflicts",
    cardSub: "Reentry date across sources",
    headline:
      "Objects whose reentry date differs across sources once parsed to a real date, so “1957 Dec 1 1000?” and “1957-12-01” don't count. The raw claims stay visible.",
  },
  {
    key: "stale",
    statsKey: "stale_owners",
    tabLabel: "Stale owners",
    cardLabel: "Stale post-M&A owners",
    cardSub: "Catalog names the acquired child",
    headline:
      "Objects whose latest catalog owner still resolves to a company that has since been acquired — the graph knows the parent; the catalog still names the child.",
  },
];

/** Coerce a raw ?tab= value to a valid ConflictTab, defaulting to "status". */
export function toConflictTab(raw: string | null): ConflictTab {
  return CONFLICT_TABS.some((t) => t.key === raw) ? (raw as ConflictTab) : "status";
}
