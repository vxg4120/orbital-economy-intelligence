import { useMemo } from "react";
import type { OwnershipSegment } from "../api/types";
import { fmtDate, fmtRangeEnd } from "../lib/format";
import { EmptyState } from "./States";

const DAY = 86_400_000;

function toMs(iso: string | null, fallback: number): number {
  if (!iso) return fallback;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? fallback : t;
}

/** Horizontal SCD Type-2 ownership bar — one track per role. Each segment is
    labeled with operator + valid range; the boundary between consecutive owner
    segments (an acquisition / transfer of custody) is marked with a dashed rule
    and its date. This is the OneWeb→Eutelsat problem made visible. */
export function OwnershipTimeline({ segments }: { segments: OwnershipSegment[] }) {
  const now = Date.now();

  const model = useMemo(() => {
    if (segments.length === 0) return null;
    const starts = segments.map((s) => toMs(s.valid_from, now));
    const ends = segments.map((s) => toMs(s.valid_to, now));
    const min = Math.min(...starts);
    // pad the open (present) end by ~4% so a current segment always has width.
    const max = Math.max(...ends, min + DAY);
    const span = Math.max(max - min, DAY);
    const padded = max + span * 0.02;
    const domain = Math.max(padded - min, DAY);

    const roles = Array.from(new Set(segments.map((s) => s.role)));
    const byRole = roles.map((role) => ({
      role,
      segs: segments
        .filter((s) => s.role === role)
        .sort((a, b) => toMs(a.valid_from, min) - toMs(b.valid_from, min))
        .map((s) => {
          const from = toMs(s.valid_from, min);
          const to = toMs(s.valid_to, padded);
          return {
            seg: s,
            left: ((from - min) / domain) * 100,
            width: Math.max(((to - from) / domain) * 100, 1.5),
            current: s.valid_to === null,
          };
        }),
    }));

    // internal boundaries (transfers) on the owner role only: a second operator or
    // manufacturer segment is not a change of custody and must not draw a transfer mark
    const boundaries = byRole
      .filter((r) => r.role === "owner")
      .flatMap((r) =>
        r.segs
          .filter((_, i) => i > 0)
          .map((s) => ({ left: s.left, date: s.seg.valid_from })),
      );
    const open = segments.some((s) => s.valid_to === null);

    return { min, max, domain, padded, byRole, boundaries, open };
  }, [segments, now]);

  if (!model) {
    return <EmptyState title="No ownership on record" message="No resolved operator segments for this object." />;
  }

  const startYear = new Date(model.min).getUTCFullYear();
  // the padding is geometry (a current segment always has width); the label is the record
  const endYear = new Date(model.max).getUTCFullYear();

  return (
    <div className="timeline">
      {model.byRole.map((r) => (
        <div className="tl-row" key={r.role}>
          <span className="tl-role">{r.role}</span>
          <div className="tl-track">
            {r.segs.map((s, i) => (
              <div
                key={`${s.seg.operator_id}-${i}`}
                className={`tl-seg${s.current ? " is-current" : ""}`}
                style={{ left: `${s.left}%`, width: `${s.width}%` }}
                title={`${s.seg.operator_name} · ${fmtDate(s.seg.valid_from)} → ${fmtRangeEnd(
                  s.seg.valid_to,
                )} · source ${s.seg.source}`}
              >
                <span className="tl-seg__op">{s.seg.operator_name}</span>
                <span className="tl-seg__range num">
                  {fmtDate(s.seg.valid_from)} → {fmtRangeEnd(s.seg.valid_to)}
                </span>
              </div>
            ))}
            {r.role === "owner" &&
              model.boundaries.map((b, i) => (
                // past the midpoint the tag hangs left of its rule (theme.css), so it never
                // runs out past the track's right edge on a phone
                <div
                  className={`tl-boundary${b.left > 50 ? " tl-boundary--late" : ""}`}
                  key={i}
                  style={{ left: `${b.left}%` }}
                >
                  <span className="tl-boundary__tag">⤳ transfer {fmtDate(b.date)}</span>
                </div>
              ))}
          </div>
        </div>
      ))}
      <div className="tl-axis">
        <span />
        <div className="tl-axis__scale">
          <span className="num">{startYear}</span>
          <span className="num">{model.open ? "present" : endYear}</span>
        </div>
      </div>
    </div>
  );
}
