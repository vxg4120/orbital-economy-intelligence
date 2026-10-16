/* =============================================================================
   Story copy for the landing. The API returns facts (api/routers/stories.py);
   these helpers phrase them as plain sentences, so copy changes never need an
   API deploy. Every number in a sentence comes from the payload.
   ============================================================================= */
import type { StoriesResponse } from "../api/types";
import { fmtInt, fmtPct } from "./format";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Presentation names for operators whose cataloged names are transliterations. */
const OPERATOR_NAMES: Record<string, string> = {
  "Shanghai Yuanxin Weixing Keji YG": "Shanghai Spacecom",
  "Zhongguo Weixing Wangluo Jituan YG": "China Satellite Network Group",
  "National Reconnaissance Office (NROC)": "National Reconnaissance Office",
};

export function operatorName(op: string | null): string {
  if (!op) return "no owner on record";
  return OPERATOR_NAMES[op] ?? op;
}

/** Constellation display: the network first, the owner it is counted by second. */
export const CONSTELLATION_NAMES: Record<string, { name: string; owner: string }> = {
  starlink: { name: "Starlink", owner: "SpaceX" },
  oneweb: { name: "OneWeb", owner: "Eutelsat" },
  kuiper: { name: "Kuiper", owner: "Amazon" },
  qianfan: { name: "Qianfan", owner: "Shanghai Spacecom" },
  guowang: { name: "Guowang", owner: "China SatNet" },
};

function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return [y, m, d ?? 1];
}

/** "2026-10-10" -> "October 10", with the year when it differs from `ref`'s. */
export function longDate(iso: string, ref?: string): string {
  const [y, m, d] = parts(iso);
  const sameYear = ref ? parts(ref)[0] === y : true;
  return `${MONTHS[m - 1]} ${d}${sameYear ? "" : `, ${y}`}`;
}

/** "2026-10-10" -> "Oct 10". */
export function shortDate(iso: string): string {
  const [, m, d] = parts(iso);
  return `${MONTHS[m - 1].slice(0, 3)} ${d}`;
}

/** "2026-06" -> "June 2026". */
export function monthYear(month: string): string {
  const [y, m] = parts(month);
  return `${MONTHS[m - 1]} ${y}`;
}

/** "2026-06" -> "Jun". */
export function monthAbbr(month: string): string {
  return MONTHS[parts(month)[1] - 1].slice(0, 3);
}

/** Whole days from a to b (ISO dates). */
export function daysBetween(a: string, b: string): number {
  const ta = Date.UTC(...ymd(a));
  const tb = Date.UTC(...ymd(b));
  return Math.round((tb - ta) / 86_400_000);
}

function ymd(iso: string): [number, number, number] {
  const [y, m, d] = parts(iso);
  return [y, m - 1, d];
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${fmtInt(n)} ${n === 1 ? one : many}`;
}

type Storm = StoriesResponse["storms"][number];

/** The strongest storm (by Kp) within the last `days` of `asOf`, and the newest earlier storm
    that was at least as strong, if the 120-day list holds one. */
export function headlineStorm(
  s: StoriesResponse,
  days = 30,
): { storm: Storm; since: Storm | null } | null {
  const recent = s.storms.filter((st) => daysBetween(st.day, s.as_of) < days);
  if (recent.length === 0) return null;
  const storm = recent.reduce((best, st) => (st.kp_max > best.kp_max ? st : best));
  const since = s.storms.find((st) => st.day < storm.day && st.kp_max >= storm.kp_max) ?? null;
  return { storm, since };
}

export function stormSentence(s: StoriesResponse): string {
  const h = headlineStorm(s);
  if (!h) {
    const last = s.storms[0];
    return last
      ? `No geomagnetic storm reached G1 in the last 30 days; the most recent was a ${last.level} on ${longDate(last.day, s.as_of)}.`
      : "No geomagnetic storm reached G1 in the last 120 days.";
  }
  const { storm, since } = h;
  const when = longDate(storm.day, s.as_of);
  return since
    ? `A ${storm.level} geomagnetic storm on ${when} was the strongest since the ${since.level} storm of ${longDate(since.day, s.as_of)}.`
    : `A ${storm.level} geomagnetic storm on ${when} was the strongest of the last 120 days.`;
}

/** The two to three sentence dispatch at the top of the landing. */
export function dispatch(s: StoriesResponse): string[] {
  const w = s.last_30d;
  const out = [
    `In the last 30 days, ${plural(w.launches, "launch", "launches")} put ${plural(
      w.payloads_launched,
      "payload",
    )} into orbit and ${plural(w.payloads_reentered, "payload")} came back down.`,
  ];
  const lead = s.active_fleet.top[0];
  if (lead && s.active_fleet.total > 0) {
    out.push(
      `${operatorName(lead.operator)} owns ${fmtPct(lead.pct)} of the ${fmtInt(
        s.active_fleet.total,
      )} active payloads.`,
    );
  }
  out.push(stormSentence(s));
  return out;
}

/** Sum of a monthly field over the twelve complete months before the current one. */
export function lastTwelve(s: StoriesResponse, key: "launched" | "reentered"): number {
  const full = s.launches_monthly.filter((m) => !m.partial).slice(-12);
  return full.reduce((n, m) => n + m[key], 0);
}
