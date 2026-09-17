import { Link } from "react-router-dom";
import type { AuditSummary } from "../api/types";
import { compact, fmtInt, fmtNum, fmtPct } from "../lib/format";
import { daysToReach, deadlineStatus, monthYear, plusDays } from "../lib/deadline";
import { Panel } from "./Panel";

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
  const deadline = deadlineStatus(k.deadline);
  // The obligation is judged on what had launched by the deadline, so a shortfall is a fixed
  // historical fact: later launches raise the progress meter, never rewrite the verdict.
  const atDeadline = k.deployed_by_deadline ?? k.deployed_total;
  const shortfall = Math.max(0, k.required - atDeadline);

  // Before the deadline: a linear projection from the trailing-30-day rate to the deadline.
  // After it, the obligation is a settled fact (met or missed by N), and the only forward
  // number left is when the current rate reaches the required count, dated from today.
  const projected = Math.round(k.deployed_total + k.deployed_last_30d * (deadline.daysLeft / 30));
  const short = !deadline.passed && projected < k.required;
  const missed = deadline.passed && shortfall > 0;
  const reachDays = daysToReach(k.deployed_total, k.required, k.deployed_last_30d);
  const reachDate = reachDays === null ? null : plusDays(new Date(), reachDays);
  const rate = `${fmtInt(k.deployed_last_30d)}/30d`;
  const projTitle = deadline.passed
    ? `Deadline ${k.deadline} passed ${deadline.daysSince}d ago with ${fmtInt(
        atDeadline,
      )} of ${fmtInt(k.required)} launched by then (${fmtInt(k.deployed_total)} today). At ${
        rate
      } the required count is reached ${
        reachDate ? `~${reachDate}` : "never (no launches in the trailing 30 days)"
      }.`
    : `Linear projection: ${fmtInt(k.deployed_total)} now + ${rate} × ${
        deadline.daysLeft
      }d = ~${fmtInt(projected)} by ${k.deadline} (need ${fmtInt(k.required)}).`;

  return (
    <Panel title="Kuiper milestone" meta={`FCC 50% · ${fmtInt(k.required)} by ${monthYear(k.deadline)}`}>
      <div className="kuiper">
        <div className="kuiper__head">
          <span className="kuiper__count num">{fmtInt(k.deployed_total)}</span>
          <span className="kuiper__req num">/ {fmtInt(k.required)} required</span>
          <span
            className={`countdown-chip${short || missed ? " is-short" : ""}`}
            title={projTitle}
          >
            {deadline.passed
              ? missed
                ? `▲ deadline passed · ${deadline.daysSince}d ago`
                : "deadline met"
              : `${short ? "▲ " : ""}${deadline.daysLeft}d left`}
          </span>
        </div>
        <div
          className="meter__track kuiper__track"
          role="meter"
          aria-valuenow={Math.round(pct)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Kuiper deployment toward ${fmtInt(k.required)}`}
        >
          <div
            className={`meter__fill${short || missed ? " is-warn" : ""}`}
            style={{ width: `${Math.min(100, pct)}%` }}
          />
        </div>
        <div className="kuiper__legend">
          <LegendDot label="at shell" n={k.at_shell} tone="active" />
          <LegendDot label="raising" n={k.raising} tone="signal" />
          <LegendDot label="deorbited" n={k.deorbited} tone="decayed" />
        </div>
        <p className="hint" title={projTitle}>
          {fmtPct(pct)} of the obligation ·{" "}
          {deadline.passed ? (
            <>
              deadline {k.deadline} passed <span className="num">{deadline.daysSince}</span>d ago —{" "}
              {missed ? (
                <>
                  <span className="audit-warn">
                    missed by <span className="num">{fmtInt(shortfall)}</span>
                  </span>{" "}
                  (<span className="num">{fmtInt(atDeadline)}</span> launched by then) · at{" "}
                  <span className="num">{rate}</span>,{" "}
                  {reachDate ? (
                    <>
                      {fmtInt(k.required)} reached ~<span className="num">{reachDate}</span>
                    </>
                  ) : (
                    "no launches in the trailing 30 days"
                  )}
                </>
              ) : (
                <span className="audit-ok">
                  obligation met with <span className="num">{fmtInt(atDeadline)}</span> launched
                  by then
                </span>
              )}
            </>
          ) : (
            <>
              projecting <span className="num">~{compact(projected)}</span> by {k.deadline} at{" "}
              <span className="num">{rate}</span> —{" "}
              <span className={short ? "audit-warn" : "audit-ok"}>
                {short ? "short of target" : "on track"}
              </span>
            </>
          )}
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
