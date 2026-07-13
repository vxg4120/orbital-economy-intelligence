/* Review-area vocabulary: stratum display order + labels, verdict badge metadata, per-stratum
   accuracy, and the source→canonical mappings that let the claims grid show "'-' → INACTIVE".

   The status map is transcribed from the DB status_mapping table; the object-type mapping mirrors
   identity/normalize.canonical_object_type (coarse type by leading character). These are small,
   stable lookups — the alternative (a network round-trip per cell) would be absurd for a table. */

import type { GoldSatelliteEvidence, ReviewStratum, Verdict } from "../api/types";

export const STRATUM_ORDER = [
  "ambiguous_cospar",
  "rideshare_orphan",
  "missed_join_candidate",
  "owner_dispute",
  "status_conflict",
  "decay_conflict",
  "type_conflict",
  "stale_owner",
];

const STRATUM_LABELS: Record<string, string> = {
  ambiguous_cospar: "Ambiguous COSPAR",
  rideshare_orphan: "Rideshare orphan",
  missed_join_candidate: "Missed-join candidate",
  owner_dispute: "Owner dispute",
  status_conflict: "Status conflict",
  decay_conflict: "Decay conflict",
  type_conflict: "Type conflict",
  stale_owner: "Stale owner",
};

export function stratumLabel(caseType: string): string {
  return STRATUM_LABELS[caseType] ?? caseType;
}

/** Presentation order used by the queue: known strata first (STRATUM_ORDER), then any others. */
export function orderStrata<T extends { case_type: string }>(strata: T[]): T[] {
  return [...strata].sort((a, b) => {
    const ia = STRATUM_ORDER.indexOf(a.case_type);
    const ib = STRATUM_ORDER.indexOf(b.case_type);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.case_type.localeCompare(b.case_type);
  });
}

/* ---- verdicts ------------------------------------------------------------- */
export interface VerdictMeta {
  key: Verdict;
  label: string;
  short: string; // single-key hint
  className: string;
}

export const VERDICT_META: VerdictMeta[] = [
  { key: "correct", label: "Correct", short: "c", className: "vd-correct" },
  { key: "incorrect", label: "Incorrect", short: "i", className: "vd-incorrect" },
  { key: "partial", label: "Partial", short: "p", className: "vd-partial" },
  { key: "unresolvable", label: "Unresolvable", short: "u", className: "vd-unresolvable" },
];

export function verdictMeta(v: Verdict | null | undefined): VerdictMeta | null {
  if (!v) return null;
  return VERDICT_META.find((m) => m.key === v) ?? null;
}

/** Verdicts that require a corrected answer (system answer is wrong/incomplete). */
export const NEEDS_CORRECTION: Verdict[] = ["incorrect", "partial"];

/* ---- accuracy-so-far (mirrors scripts/score_gold: correct=1, partial=0.5, /gradable) ---- */
export function stratumAccuracy(s: ReviewStratum): number | null {
  const gradable = s.labeled - s.unresolvable;
  if (gradable <= 0) return null;
  return (s.correct + 0.5 * s.partial) / gradable;
}

/* ---- source → canonical mappings (for the claims grid annotation) --------- */
const STATUS_MAP: Record<string, Record<string, string>> = {
  satcat: {
    "+": "ACTIVE", "-": "INACTIVE", "?": "UNKNOWN", B: "SPARE", D: "DECAYED",
    P: "PARTIAL", S: "SPARE", X: "ACTIVE",
  },
  gcat: {
    AF: "DECAYED", AL: "DECAYED", "AL IN": "DECAYED", AO: "UNKNOWN", "AO IN": "UNKNOWN",
    AR: "DECAYED", "AR IN": "DECAYED", AS: "DECAYED", ATT: "UNKNOWN", C: "DECAYED", D: "DECAYED",
    DEP: "UNKNOWN", DK: "UNKNOWN", DSA: "UNKNOWN", DSO: "UNKNOWN", E: "DECAYED", EO: "UNKNOWN",
    ERR: "UNKNOWN", F: "DECAYED", GRP: "UNKNOWN", L: "DECAYED", LEASE: "UNKNOWN", LF: "DECAYED",
    N: "UNKNOWN", NA: "UNKNOWN", O: "UNKNOWN", OI: "UNKNOWN", OX: "UNKNOWN", R: "DECAYED",
    REL: "UNKNOWN", S: "DECAYED", TX: "DECAYED",
  },
  ucs: { OPERATIONAL: "ACTIVE" },
};

const OBJECT_TYPE_BY_CHAR: Record<string, string> = {
  P: "PAYLOAD", S: "PAYLOAD", R: "ROCKET_BODY", C: "DEBRIS", D: "DEBRIS", X: "UNKNOWN", Z: "UNKNOWN",
};

function canonicalObjectType(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const t = raw.trim().toUpperCase();
  if (!t) return null;
  return OBJECT_TYPE_BY_CHAR[t[0]] ?? "UNKNOWN";
}

/** Build a `${attribute}|${source}` → canonical-annotation map for one satellite's claims.

    Prefers the stratum-specific evidence blocks (authoritative, already canonicalized by the
    selector) and falls back to the generic status / object-type mappings so status and type cells
    annotate even outside their own stratum. Returns only meaningful annotations (skips echoes and
    UNKNOWNs that add nothing). */
export function buildCanonicalMap(ev: GoldSatelliteEvidence): Map<string, string> {
  const out = new Map<string, string>();
  const put = (attr: string, source: string, canon: string | null | undefined, raw?: string) => {
    if (!canon) return;
    if (raw !== undefined && canon.trim().toUpperCase() === raw.trim().toUpperCase()) return;
    out.set(`${attr}|${source}`, canon);
  };

  // Generic mappings over every claim.
  for (const a of ev.assertions ?? []) {
    if (a.attribute === "status") {
      put("status", a.source, STATUS_MAP[a.source]?.[a.value?.trim().toUpperCase()], a.value);
    } else if (a.attribute === "object_type") {
      put("object_type", a.source, canonicalObjectType(a.value), a.value);
    }
  }

  // Authoritative stratum blocks override / add.
  const sc = ev.status_conflict as Record<string, string> | undefined;
  if (sc) {
    put("status", "satcat", sc.satcat_canonical, sc.satcat_raw);
    put("status", "gcat", sc.gcat_canonical, sc.gcat_raw);
  }
  const dc = ev.decay_conflict as Record<string, string> | undefined;
  if (dc) {
    put("decay_date", "satcat", dc.satcat_parsed, dc.satcat_raw);
    put("decay_date", "gcat", dc.gcat_parsed, dc.gcat_raw);
  }
  const od = ev.owner_dispute as Record<string, string> | undefined;
  if (od) {
    put("owner", "satcat", od.satcat_operator, od.satcat_code);
    put("owner", "gcat", od.gcat_operator, od.gcat_code);
  }
  const tc = ev.type_conflict as Record<string, string> | undefined;
  if (tc) {
    put("object_type", "satcat", canonicalObjectType(tc.satcat_raw), tc.satcat_raw);
    put("object_type", "gcat", canonicalObjectType(tc.gcat_raw), tc.gcat_raw);
  }
  return out;
}
