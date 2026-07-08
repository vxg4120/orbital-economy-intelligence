import type { ReactNode } from "react";

interface StatTileProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  /** lead tile gets the signal-colored top rule + optional hero sizing. */
  lead?: boolean;
  hero?: boolean;
}

/** Stat tile: micro label, large tabular value, optional context line. */
export function StatTile({ label, value, sub, lead, hero }: StatTileProps) {
  return (
    <div className={`stat${lead ? " stat--lead" : ""}`}>
      <span className="stat__label">{label}</span>
      <span className={`stat__value num${hero ? " is-hero" : ""}`}>{value}</span>
      {sub !== undefined ? <span className="stat__sub">{sub}</span> : null}
    </div>
  );
}
