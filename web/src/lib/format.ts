/* Formatting helpers. NULL-safe throughout — every nullable field renders as an
   em dash so analyst objects (norad_id null) and empty ranges never show blanks. */

export const DASH = "—"; // em dash

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return DASH;
  return n.toLocaleString("en-US");
}

/** Auto-compact large counts: 1,284 / 12.9K / 4.2M / 1.3B. */
export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined) return DASH;
  const abs = Math.abs(n);
  if (abs < 10_000) return n.toLocaleString("en-US");
  if (abs < 1_000_000) return trim(n / 1_000) + "K";
  if (abs < 1_000_000_000) return trim(n / 1_000_000) + "M";
  return trim(n / 1_000_000_000) + "B";
}

function trim(x: number): string {
  return (Math.round(x * 10) / 10).toString();
}

export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined) return DASH;
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined) return DASH;
  return n.toFixed(digits) + "%";
}

/** ISO date (YYYY-MM-DD) or null -> that date, or em dash. */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return iso.slice(0, 10);
}

/** ISO timestamp -> "YYYY-MM-DD HH:MM UTC". */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(
    d.getUTCHours(),
  )}:${p(d.getUTCMinutes())} UTC`;
}

/** "Present" for an open-ended (null) valid_to. */
export function fmtRangeEnd(iso: string | null | undefined): string {
  return iso ? fmtDate(iso) : "present";
}
