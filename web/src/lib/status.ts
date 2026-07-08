/* Reserved status + source vocabulary. Status colors live in theme.css as the
   `--st-*` scale; this module only maps a value to its class + glyph label so a
   status always reads as glyph + text, never color alone. */

export interface StatusMeta {
  className: string;
  label: string;
}

const STATUS: Record<string, StatusMeta> = {
  ACTIVE: { className: "badge--active", label: "Active" },
  PARTIAL: { className: "badge--partial", label: "Partial" },
  SPARE: { className: "badge--spare", label: "Spare" },
  INACTIVE: { className: "badge--inactive", label: "Inactive" },
  GRAVEYARD: { className: "badge--graveyard", label: "Graveyard" },
  DECAYED: { className: "badge--decayed", label: "Decayed" },
  UNKNOWN: { className: "badge--unknown", label: "Unknown" },
};

export function statusMeta(status: string | null | undefined): StatusMeta {
  if (!status) return STATUS.UNKNOWN;
  return STATUS[status.toUpperCase()] ?? { className: "badge--unknown", label: status };
}

/** Source -> left-tick class for the source badge. */
export function sourceClass(source: string): string {
  const key = source.toLowerCase().split(/[_\s]/)[0];
  const known = ["satcat", "gcat", "ucs", "resolve", "celestrak", "spacetrack", "supgp"];
  return known.includes(key) ? `src-${key}` : "src-resolve";
}

/** Fleet regime ordering for consistent chart/legend order. */
export const REGIME_ORDER = ["LEO", "MEO", "GEO", "HEO"];

/** Ledger run status -> dot color class. */
export function runStatusClass(status: string): string {
  const s = status.toLowerCase();
  if (s === "ok") return "run-ok";
  if (s === "error") return "run-err";
  return "run-skip";
}
