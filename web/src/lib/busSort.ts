import type { BusSort } from "../api/types";

export type SortDir = "asc" | "desc";

/** Descending metric sorts; everything else (tto, station_keeping, name) ranks ascending.
    These are the natural directions the API applies when no dir is sent, mirrored here so a
    fresh column click lands on the same order the API would pick on its own. */
export const DESC_SORTS: BusSort[] = [
  "fleet", "on_orbit", "active", "sk_share", "decayed_share", "lifetime", "compliance", "coverage",
];

export function naturalDir(sort: BusSort): SortDir {
  return DESC_SORTS.includes(sort) ? "desc" : "asc";
}
