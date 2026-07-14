import { Link } from "react-router-dom";
import type { AuditSummary } from "../api/types";
import { compact, fmtInt, fmtNum, fmtPct } from "../lib/format";
import { Panel } from "./Panel";

const DAY_MS = 86_400_000;
const INACTIVE = "#78828f"; // reserved status hue for INACTIVE — the loitering objects

/** The AUDIT row: three live reads that carry the auditor thesis — Kuiper's deployment gap, the
    inactive payloads left loitering high, and the objects the catalog still calls active while the
    physics says they are falling. */
export function AuditStrip({ summary }: { summary: AuditSummary }) {
  return (
    <div className="grid grid--3 audit-row">
      <KuiperMilestone summary={summary} />
      <LingeringLeaderboard summary={summary} />
      <DecayTile count={summary.active_but_decaying} />
    </div>
  );
}

function KuiperMilestone({ summary }: { summary: AuditSummary }) {
  const k = summary.kuiper_milestone;
  const pct = k.required > 0 ? (k.deployed_total / k.required) * 100 : 0;

  // Days to the FCC deadline, and a simple linear projection from the trailing-30-day rate.
  const daysLeft = Math.max(
    0,
    Math.ceil((Date.parse(k.deadline + "T00:00:00Z") - Date.now()) / DAY_MS),
  );
  const projected = Math.round(k.deployed_total + k.deployed_last_30d * (daysLeft / 30));
  const short = projected < k.required;
  const projTitle = `Linear projection: ${fmtInt(k.deployed_total)} now + ${fmtInt(
    k.deployed_last_30d,
  )}/30d × ${daysLeft}d = ~${fmtInt(projected)} by ${k.deadline} (need ${fmtInt(k.required)}).`;

  return (
    <Panel title="Kuiper milestone" meta="FCC 50% · 1,618 by Jul 2026">
      <div className="kuiper">
        <div className="kuiper__head">
          <span className="kuiper__count num">{fmtInt(k.deployed_total)}</span>
          <span className="kuiper__req num">/ {fmtInt(k.required)} required</span>
          <span
            className={`countdown-chip${short ? " is-short" : ""}`}
            title={projTitle}
          >
            {short ? "▲ " : ""}
            {daysLeft}d left
          </span>
        </div>
        <div
          className="meter__track kuiper__track"
          role="meter"
          aria-valuenow={Math.round(pct)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Kuiper deployment toward 1,618"
        >
          <div className={`meter__fill${short ? " is-warn" : ""}`} style={{ width: `${pct}%` }} />
        </div>
        <div className="kuiper__legend">
          <LegendDot label="at shell" n={k.at_shell} tone="active" />
          <LegendDot label="raising" n={k.raising} tone="signal" />
          <LegendDot label="deorbited" n={k.deorbited} tone="decayed" />
        </div>
        <p className="hint" title={projTitle}>
          {fmtPct(pct)} of the obligation · projecting{" "}
          <span className="num">~{compact(projected)}</span> by {k.deadline} at{" "}
          <span className="num">{fmtInt(k.deployed_last_30d)}</span>/30d —{" "}
          <span className={short ? "audit-warn" : "audit-ok"}>
            {short ? "short of target" : "on track"}
          </span>
        </p>
      </div>
    </Panel>
  );
}

function LegendDot({ label, n, tone }: { label: string; n: number; tone: string }) {
  return (
    <span className="kuiper__leg">
      <span className={`kuiper__dot kuiper__dot--${tone}`} aria-hidden="true" />
      {label} <span className="num mono-hi">{fmtInt(n)}</span>
    </span>
  );
}

function LingeringLeaderboard({ summary }: { summary: AuditSummary }) {
  const rows = summary.lingering_leaderboard;
  const max = rows.reduce((m, r) => Math.max(m, r.count), 0) || 1;
  return (
    <Panel title="Lingering LEO payloads" meta="inactive · perigee > 500 km">
      {rows.length === 0 ? (
        <p className="hint">No inactive payloads loitering above 500 km.</p>
      ) : (
        <div className="lingerbars">
          {rows.map((r) => (
            <div className="lingerbar" key={r.operator} title={`${r.operator}: ${fmtInt(r.count)} inactive · mean alt ${fmtNum(r.avg_alt_km, 1)} km`}>
              <span className="lingerbar__op">{r.operator}</span>
              <div className="lingerbar__track">
                <div
                  className="lingerbar__fill"
                  style={{ width: `${(r.count / max) * 100}%`, background: INACTIVE }}
                />
              </div>
              <span className="lingerbar__val num">
                {fmtInt(r.count)}
                <span className="lingerbar__alt"> · {fmtNum(r.avg_alt_km, 0)} km</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function DecayTile({ count }: { count: number }) {
  return (
    <Link to="/conflicts" className="decay-tile" title="Objects the catalog calls ACTIVE that have left their plateau and are still sinking — open Conflicts">
      <span className="decay-tile__label">catalog says active, physics says decaying</span>
      <span className="decay-tile__value num">{fmtInt(count)}</span>
      <span className="decay-tile__sub">
        objects in post-plateau decay <span className="decay-tile__arrow" aria-hidden="true">→</span>
      </span>
    </Link>
  );
}
