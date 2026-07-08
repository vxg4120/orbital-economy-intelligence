import { fmtPct } from "../lib/format";

interface CoverageMeterProps {
  label: string;
  /** 0–100. */
  pct: number;
  /** optional numerator/denominator caption, e.g. "4,930 / 5,332". */
  foot?: string;
}

/** Horizontal coverage meter. Fill carries severity (signal when healthy,
    amber < 70, rust < 45); the track is a dim step of the same blue ramp so the
    state reads across the whole bar (dataviz meter spec). */
export function CoverageMeter({ label, pct, foot }: CoverageMeterProps) {
  const clamped = Math.max(0, Math.min(100, pct));
  const sev = clamped < 45 ? "is-low" : clamped < 70 ? "is-warn" : "";
  return (
    <div className="meter">
      <div className="meter__head">
        <span className="meter__label">{label}</span>
        <span className="meter__val">{fmtPct(clamped)}</span>
      </div>
      <div
        className="meter__track"
        role="meter"
        aria-valuenow={Math.round(clamped)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div className={`meter__fill ${sev}`} style={{ width: `${clamped}%` }} />
      </div>
      {foot ? <span className="meter__foot">{foot}</span> : null}
    </div>
  );
}
