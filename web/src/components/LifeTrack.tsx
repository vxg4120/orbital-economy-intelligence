import { useMemo } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { LifeTrack as LifeTrackData } from "../api/types";
import { fmtNum } from "../lib/format";
import { EmptyState } from "./States";

const SIGNAL = "#4da6ff";
const BAND = "rgba(77, 166, 255, 0.12)";
const GRID = "#1b212c";
const EARTH_RADIUS_KM = 6378.137; // sma is a radius; subtract to plot it on the altitude axis

interface Row {
  day: string;
  smaAlt: number | null; // sma expressed as altitude, so it sits inside the perigee..apogee band
  sma: number | null; // raw semi-major axis (radius), shown in the tooltip
  perigee: number | null;
  apogee: number | null;
  band: [number, number] | null;
  elsets: number;
}

/** MMM'YY from an ISO date (mono, terse — the instrument look). */
function fmtMonth(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][
    d.getUTCMonth()
  ];
  return `${mon}'${String(d.getUTCFullYear()).slice(2)}`;
}

/** LifeTrack: one satellite's daily orbit history. The semi-major axis (signal line) rides inside a
    faint perigee..apogee band; a plateau then a clean break into decay is the "physics knows before
    the catalog" story made visible. Altitude framing (sma minus Earth radius) puts the line and the
    band on one scale. */
export function LifeTrack({
  data,
  variant = "full",
}: {
  data: LifeTrackData;
  variant?: "compact" | "full";
}) {
  const { rows, domain } = useMemo(() => {
    const rows: Row[] = data.points.map((p) => ({
      day: p.day,
      smaAlt: p.sma_km !== null ? p.sma_km - EARTH_RADIUS_KM : null,
      sma: p.sma_km,
      perigee: p.perigee_km,
      apogee: p.apogee_km,
      band: p.perigee_km !== null && p.apogee_km !== null ? [p.perigee_km, p.apogee_km] : null,
      elsets: p.elsets,
    }));
    let lo = Infinity;
    let hi = -Infinity;
    for (const r of rows) {
      for (const v of [r.smaAlt, r.perigee, r.apogee]) {
        if (v !== null) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
    }
    if (!Number.isFinite(lo)) return { rows, domain: [0, 1] as [number, number] };
    const pad = Math.max((hi - lo) * 0.08, 2);
    return { rows, domain: [Math.floor(lo - pad), Math.ceil(hi + pad)] as [number, number] };
  }, [data.points]);

  if (rows.length === 0) {
    return (
      <EmptyState
        title="no element-set history"
        message="No daily orbit series on record for this object."
      />
    );
  }

  const height = variant === "compact" ? 220 : 360;

  return (
    <div className="lifetrack">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={{ top: 8, right: 14, bottom: 4, left: 4 }}>
          <CartesianGrid vertical={false} stroke={GRID} strokeDasharray="2 3" />
          <XAxis
            dataKey="day"
            tickFormatter={fmtMonth}
            tickLine={false}
            axisLine={{ stroke: GRID }}
            minTickGap={variant === "compact" ? 44 : 60}
            tick={{ fontSize: 10 }}
          />
          <YAxis
            domain={domain}
            width={40}
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10 }}
            tickFormatter={(v: number) => String(Math.round(v))}
            allowDecimals={false}
          />
          <Tooltip
            isAnimationActive={false}
            cursor={{ stroke: SIGNAL, strokeWidth: 1, strokeOpacity: 0.4 }}
            content={({ active, payload }) => {
              if (!active || !payload || payload.length === 0) return null;
              const r = payload[0].payload as Row;
              return (
                <div className="hm-tip" style={{ position: "static" }}>
                  <div className="lifetrack__tipday num">{r.day}</div>
                  <TipRow k="sma" v={`${fmtNum(r.sma, 1)} km`} />
                  <TipRow k="mean alt" v={`${fmtNum(r.smaAlt, 1)} km`} />
                  <TipRow k="perigee" v={`${fmtNum(r.perigee, 1)} km`} />
                  <TipRow k="apogee" v={`${fmtNum(r.apogee, 1)} km`} />
                  <TipRow k="elsets" v={String(r.elsets)} />
                </div>
              );
            }}
          />
          <Area
            dataKey="band"
            stroke="none"
            fill={BAND}
            isAnimationActive={false}
            connectNulls
            activeDot={false}
            legendType="none"
          />
          <Line
            dataKey="smaAlt"
            stroke={SIGNAL}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="lifetrack__foot">
        <span className="hint">
          semi-major axis (mean altitude) · faint band = perigee → apogee
        </span>
        <span className="hint num">
          {data.norad_id !== null ? `NORAD ${data.norad_id} · ` : ""}
          {data.span_days} d · {data.points.length} pts
        </span>
      </div>
    </div>
  );
}

function TipRow({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <span className="hm-tip__k">{k} </span>
      <span className="hm-tip__v">{v}</span>
    </div>
  );
}
