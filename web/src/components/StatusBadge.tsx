import { statusMeta } from "../lib/status";

/** Canonical status as a mono badge: reserved status hue on the glyph + a text
    label, so state never reads by color alone. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  const meta = statusMeta(status);
  return (
    <span className={`badge badge--status ${meta.className}`} title={`Status: ${meta.label}`}>
      <span className="badge__glyph" aria-hidden="true" />
      {meta.label}
    </span>
  );
}
