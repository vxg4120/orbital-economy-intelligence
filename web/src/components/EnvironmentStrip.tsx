import type { EnvironmentResponse } from "../api/types";
import { fmtInt } from "../lib/format";

/** The drag environment: daily fleet-median SMA change as downward bars (the atmosphere
 *  taking meters off ~15k LEO orbits), storm days tinted, forward predicted days marked.
 *  Hand-rolled SVG rather than the chart library: the series is one value per day and the
 *  terminal aesthetic wants bars, a zero line, and nothing else. */
export function EnvironmentStrip({ env }: { env: EnvironmentResponse }) {
  const observed = env.rows.filter((r) => r.median_dsma_m !== null);
  const latest = env.latest_observed;
  const worst = env.worst_drag_day;

  const W = 640;
  const H = 96;
  const zeroY = 18; // room above the zero line for the rare positive median
  const maxDrop = Math.max(20, ...observed.map((r) => Math.abs(r.median_dsma_m ?? 0)));
  const barW = observed.length > 0 ? Math.min(10, (W - 4) / observed.length) : 10;

  return (
    <div>
      <div className="grid grid--stats">
        <StatCell
          label="Geomagnetic (latest observed)"
          value={latest ? `Kp ${latest.kp_max ?? "?"}` : "no data"}
          sub={
            latest?.storm_level
              ? `${latest.storm_level} storm · Ap peak ${latest.ap_max ?? "?"} · ${latest.day}`
              : latest
                ? `quiet · Ap ${latest.ap_avg ?? "?"} · ${latest.day}`
                : ""
          }
        />
        <StatCell
          label="Solar flux F10.7 (81-day)"
          value={latest?.f107_obs_center81 != null ? `${latest.f107_obs_center81} sfu` : "no data"}
          sub={latest?.f107_obs != null ? `daily ${latest.f107_obs} sfu` : ""}
        />
        <StatCell
          label="Worst drag day shown"
          value={worst?.median_dsma_m != null ? `${worst.median_dsma_m} m/day` : "no data"}
          sub={
            worst
              ? `${worst.day}${worst.storm_level ? ` · ${worst.storm_level} storm` : ""} · median of ${fmtInt(worst.sats_observed ?? 0)} sats`
              : ""
          }
        />
      </div>

      {observed.length > 0 ? (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          height={H}
          role="img"
          aria-label="Daily fleet-median semi-major-axis change, storm days highlighted"
          style={{ display: "block", marginTop: 8 }}
        >
          <line x1={0} y1={zeroY} x2={W} y2={zeroY} stroke="var(--rule)" strokeWidth={1} />
          {observed.map((r, i) => {
            const v = r.median_dsma_m ?? 0;
            const h = (Math.abs(v) / maxDrop) * (H - zeroY - 14);
            const x = 2 + i * ((W - 4) / observed.length);
            const y = v <= 0 ? zeroY : zeroY - h;
            const storm = r.storm_level !== null;
            return (
              <g key={r.day}>
                <rect
                  x={x}
                  y={y}
                  width={Math.max(2, barW - 2)}
                  height={Math.max(1, h)}
                  fill={storm ? "var(--conflict)" : "var(--signal-dim)"}
                  opacity={storm ? 0.95 : 0.65}
                >
                  <title>
                    {`${r.day}: ${v} m/day` +
                      (r.storm_level ? ` (${r.storm_level} storm, Kp ${r.kp_max})` : "")}
                  </title>
                </rect>
                {storm ? (
                  <text
                    x={x + barW / 2}
                    y={H - 2}
                    textAnchor="middle"
                    fontSize={8}
                    fill="var(--conflict)"
                  >
                    {r.storm_level}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
      ) : null}
    </div>
  );
}

function StatCell({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div>
      <span className="label">{label}</span>
      <div className="mono-hi" style={{ fontSize: 18, marginTop: 2 }}>{value}</div>
      {sub ? <div className="hint">{sub}</div> : null}
    </div>
  );
}
