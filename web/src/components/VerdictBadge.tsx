import type { Verdict } from "../api/types";
import { verdictMeta } from "../lib/reviewStrata";

/** A gold-verdict as a mono badge: glyph + label, reserved verdict hue on the border/glyph so the
    verdict never reads by color alone. */
export function VerdictBadge({ verdict }: { verdict: Verdict | null | undefined }) {
  const meta = verdictMeta(verdict);
  if (!meta) return null;
  return (
    <span className={`badge badge--verdict ${meta.className}`} title={`Verdict: ${meta.label}`}>
      <span className="badge__glyph" aria-hidden="true" />
      {meta.label}
    </span>
  );
}
