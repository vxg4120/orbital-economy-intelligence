import type { AuditSummary } from "../api/types";
import { compact, fmtInt, fmtNum, fmtPct } from "../lib/format";
import { daysToReach, deadlineStatus, monthYear, plusDays } from "../lib/deadline";
import { Panel } from "./Panel";

const INACTIVE = "#78828f"; // reserved status hue for INACTIVE — the loitering objects

/** The FCC's action on the July 2026 Kuiper milestone, read from the order itself (DA 26-553,
    released 2026-06-05). It applies to that one deadline, so the note shows only when the API's
    deadline is this one and the milestone was missed. */
const KUIPER_ORDER = {
  deadline: "2026-07-30",
  id: "DA 26-553",
  url: "https://docs.fcc.gov/public/attachments/DA-26-553A1.pdf",
};

/** The fleets the lingering panel covers. They mirror BENCHMARK_OPERATORS in api/routers/audit.py,
    which are the fleets whose Space-Track element history is loaded; tests/test_orbital_pages_copy_claims.py
    fails if the two lists drift. */
const LINGERING_FLEETS_NOTE =
  "Only the eight fleets whose Space-Track element history has been loaded since mid-2025 are counted: SpaceX, Eutelsat with OneWeb, Planet Labs, Spire, Iridium, ICEYE, Capella Space and Amazon.";

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
  // The obligation is judged on what was in orbit on the deadline, so a shortfall is a fixed
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
      )} of ${fmtInt(k.required)} in orbit on that date (${fmtInt(k.deployed_total)} launched to date). At ${
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
              deadline {k.deadline} passed <span className="num">{deadline.daysSince}</span>d ago,{" "}
              {missed ? (
                <>
                  <span className="audit-warn">
                    missed by <span className="num">{fmtInt(shortfall)}</span>
                  </span>{" "}
                  (<span className="num">{fmtInt(atDeadline)}</span> in orbit on that date) · at{" "}
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
                  obligation met with <span className="num">{fmtInt(atDeadline)}</span> in orbit
                  on that date
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
        {missed && k.deadline === KUIPER_ORDER.deadline ? (
          <p className="hint">
            The FCC did not extend this deadline, but its June 2026 order (
            <a href={KUIPER_ORDER.url} target="_blank" rel="noreferrer">
              {KUIPER_ORDER.id}
            </a>
            ) waived the rule that would have capped Amazon's authorization at the satellites that
            were launched and operating on that date, so Amazon can still launch the rest and has until July 30, 2029 to
            deploy all 3,232. The miss still forfeits Amazon's surety bond, and satellites that were
            not operating by the deadline lose their spectrum priority until March 30, 2028 at the
            latest.
          </p>
        ) : null}
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
    <Panel title="Lingering LEO payloads" meta="inactive · perigee above 500 km · eight fleets">
      {rows.length === 0 ? (
        <p className="hint">None of the eight fleets has an inactive payload loitering above 500 km.</p>
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
      <p className="hint">
        {LINGERING_FLEETS_NOTE} A fleet with no lingering payloads is not listed.
      </p>
    </Panel>
  );
}

/** A figure, not a link: no view lists these objects (the Conflicts tabs are cross-catalog
    disagreements, a different dataset), so a link there landed on the wrong rows. */
function DecayTile({ count }: { count: number }) {
  return (
    <div
      className="decay-tile"
      title="Objects the catalog calls ACTIVE whose daily orbit has left its plateau and is still sinking, from the GP element history"
    >
      <span className="decay-tile__label">catalog says active, physics says decaying</span>
      <span className="decay-tile__value num">{fmtInt(count)}</span>
      <span className="decay-tile__sub">objects in post-plateau decay</span>
    </div>
  );
}
