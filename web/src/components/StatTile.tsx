import type { ReactNode } from "react";
import { compact, fmtInt } from "../lib/format";

interface StatTileProps {
  label: string;
  /** Rendered value. Omit it and pass `n` for a count: the tile shows compact(n) and
      keeps the exact figure in the value's title. */
  value?: ReactNode;
  n?: number | null;
  sub?: ReactNode;
  /** lead tile gets the signal-colored top rule + optional hero sizing. */
  lead?: boolean;
  hero?: boolean;
}

/** Stat tile: micro label, large tabular value, optional context line. */
export function StatTile({ label, value, n, sub, lead, hero }: StatTileProps) {
  // title only when compacting dropped precision (71.1K, not 1,444)
  const exact = n !== undefined && n !== null && compact(n) !== fmtInt(n) ? fmtInt(n) : undefined;
  return (
    <div className={`stat${lead ? " stat--lead" : ""}`}>
      <span className="stat__label">{label}</span>
      <span className={`stat__value num${hero ? " is-hero" : ""}`} title={exact}>
        {value ?? compact(n)}
      </span>
      {sub !== undefined ? <span className="stat__sub">{sub}</span> : null}
    </div>
  );
}
