import { useMemo, useState } from "react";
import type { CongestionBin } from "../api/types";
import { fmtInt } from "../lib/format";

/* Sequential blue ramp (theme --seq-1..6). Near-zero recedes to the surface;
   density climbs light→bright. Buckets are log-scaled because orbital shells are
   heavily skewed (one Starlink band dwarfs everything). */
const RAMP = ["#14283c", "#17405f", "#1c5688", "#2472b3", "#3a92dd", "#62b4ff"];
const EMPTY = "#0c1016";
const ALT_STEP = 50;
const INC_STEP = 5;
const INC_MAX = 180;

interface HoverCell {
  altLow: number;
  incLow: number;
  count: number;
  x: number;
  y: number;
}

interface Props {
  bins: CongestionBin[];
  maxAltKm?: number;
  cellW?: number;
  cellH?: number;
}

function bucket(count: number, max: number): number {
  if (count <= 0) return -1;
  const t = Math.log(count + 1) / Math.log(max + 1);
  return Math.min(RAMP.length - 1, Math.max(0, Math.ceil(t * RAMP.length) - 1));
}

export function CongestionHeatmap({ bins, maxAltKm = 2000, cellW = 13, cellH = 9 }: Props) {
  const [hover, setHover] = useState<HoverCell | null>(null);

  const { lookup, maxCount } = useMemo(() => {
    const map = new Map<string, number>();
    let mx = 0;
    for (const b of bins) {
      if (b.alt_bin_km >= maxAltKm) continue;
      map.set(`${b.alt_bin_km}:${b.inc_bin_deg}`, b.object_count);
      if (b.object_count > mx) mx = b.object_count;
    }
    return { lookup: map, maxCount: mx };
  }, [bins, maxAltKm]);

  const rows = Math.round(maxAltKm / ALT_STEP);
  const cols = Math.round(INC_MAX / INC_STEP);
  const mL = 46;
  const mT = 6;
  const mB = 36;
  const mR = 8;
  const plotW = cols * cellW;
  const plotH = rows * cellH;
  const w = mL + plotW + mR;
  const h = mT + plotH + mB;

  const xTicks = [0, 30, 60, 90, 120, 150, 180];
  const yTicks: number[] = [];
  for (let a = 0; a <= maxAltKm; a += 500) yTicks.push(a);

  const cells: React.ReactNode[] = [];
  for (let i = 0; i < rows; i++) {
    const altLow = maxAltKm - (i + 1) * ALT_STEP;
    const y = mT + i * cellH;
    for (let j = 0; j < cols; j++) {
      const incLow = j * INC_STEP;
      const x = mL + j * cellW;
      const count = lookup.get(`${altLow}:${incLow}`) ?? 0;
      const b = bucket(count, maxCount);
      const fill = b < 0 ? EMPTY : RAMP[b];
      const isHover = hover?.altLow === altLow && hover?.incLow === incLow;
      cells.push(
        <rect
          key={`${i}-${j}`}
          className={`hm-cell${isHover ? " is-hover" : ""}`}
          x={x}
          y={y}
          width={cellW}
          height={cellH}
          fill={fill}
          onMouseEnter={(e) =>
            setHover({ altLow, incLow, count, x: e.clientX, y: e.clientY })
          }
          onMouseMove={(e) =>
            setHover({ altLow, incLow, count, x: e.clientX, y: e.clientY })
          }
          onMouseLeave={() => setHover(null)}
        />,
      );
    }
  }

  return (
    <div className="heatmap">
      <div className="heatmap__plot">
        <svg className="hm-svg" width={w} height={h} role="img" aria-label="Orbital congestion by altitude and inclination">
          {/* y ticks + labels */}
          {yTicks.map((a) => {
            const y = mT + (rows - a / ALT_STEP) * cellH;
            return (
              <text key={a} className="hm-axis-label" x={mL - 6} y={y + 3} textAnchor="end">
                {a}
              </text>
            );
          })}
          {/* x ticks + labels */}
          {xTicks.map((d) => (
            <text
              key={d}
              className="hm-axis-label"
              x={mL + (d / INC_STEP) * cellW}
              y={mT + plotH + 14}
              textAnchor="middle"
            >
              {d}
            </text>
          ))}
          {cells}
          {/* axis titles */}
          <text className="hm-axis-title" x={mL + plotW / 2} y={h - 4} textAnchor="middle">
            Inclination (°)
          </text>
          <text
            className="hm-axis-title"
            x={12}
            y={mT + plotH / 2}
            textAnchor="middle"
            transform={`rotate(-90 12 ${mT + plotH / 2})`}
          >
            Altitude (km)
          </text>
        </svg>
      </div>

      <div className="hm-foot">
        <span className="hint">
          LEO focus · {fmtInt(bins.length)} occupied bins · {ALT_STEP} km × {INC_STEP}° · peak{" "}
          <span className="num">{fmtInt(maxCount)}</span> objects
        </span>
        <div className="hm-legend">
          <span className="hm-legend__label">low</span>
          <div className="hm-legend__ramp">
            {RAMP.map((c) => (
              <span key={c} className="hm-legend__swatch" style={{ background: c }} />
            ))}
          </div>
          <span className="hm-legend__label">high (log)</span>
        </div>
      </div>

      {hover ? (
        <div className="hm-tip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
          <div>
            <span className="hm-tip__k">alt </span>
            <span className="hm-tip__v">
              {hover.altLow}–{hover.altLow + ALT_STEP} km
            </span>
          </div>
          <div>
            <span className="hm-tip__k">inc </span>
            <span className="hm-tip__v">
              {hover.incLow}–{hover.incLow + INC_STEP}°
            </span>
          </div>
          <div>
            <span className="hm-tip__k">objects </span>
            <span className="hm-tip__v">{hover.count > 0 ? fmtInt(hover.count) : "0"}</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
