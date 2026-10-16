import { useId } from "react";
import { Link } from "react-router-dom";
import type { EnvironmentResponse, StoriesResponse } from "../api/types";
import { useWidth } from "../hooks/useWidth";
import { fmtInt, fmtPct } from "../lib/format";
import {
  CONSTELLATION_NAMES,
  daysBetween,
  lastTwelve,
  longDate,
  monthAbbr,
  monthYear,
  operatorName,
  plural,
  shortDate,
  stormSentence,
} from "../lib/stories";
import { Panel } from "./Panel";

/* =============================================================================
   The landing's story panels. Each one is a headline sentence computed from
   /api/stories, one visual, and a one-line note on how the numbers were made.
   Charts are hand-rolled SVG drawn in real pixels (useWidth), so labels keep
   their size on a phone; every chart carries role="img", an aria-label summary,
   <title> tooltips and a screen-reader table.
   ============================================================================= */

/** A column with a 4px rounded data end and a square foot on the baseline. */
function colPath(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** Clean axis ticks from zero: steps of 1, 2 or 5 times a power of ten, three or four of them. */
function niceTicks(max: number, target = 3): number[] {
  const raw = Math.max(1, max) / target;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((k) => k * pow).find((s) => s >= raw) ?? 10 * pow;
  const top = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= top + 1e-9; v += step) ticks.push(v);
  return ticks;
}

function compactTick(v: number): string {
  return v >= 1000 ? `${Math.round(v / 100) / 10}K` : String(v);
}

/* ---- a. Launch tempo ------------------------------------------------------- */

export function LaunchTempo({ s }: { s: StoriesResponse }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const hatch = useId().replace(/:/g, "");
  const months = s.launches_monthly;
  const full = months.filter((m) => !m.partial).slice(-12);
  const launched12 = lastTwelve(s, "launched");
  const reentered12 = lastTwelve(s, "reentered");
  const partial = months.find((m) => m.partial);
  const peak = months.reduce((a, m) => (m.launched > a.launched ? m : a), months[0]);

  const H = 230;
  const mT = 20;
  const mB = 36;
  const mL = 36;
  const mR = 2;
  const plotH = H - mT - mB;
  const plotW = Math.max(0, W - mL - mR);
  const ticks = niceTicks(Math.max(...months.map((m) => Math.max(m.launched, m.reentered))));
  const yMax = ticks[ticks.length - 1] || 1;
  const band = months.length ? plotW / months.length : 0;
  const barW = Math.max(3, Math.min(16, (band - 8) / 2));
  const y = (v: number) => mT + plotH - (v / yMax) * plotH;
  const labelEvery = band >= 34 ? 1 : 2;
  const lastFull = full[full.length - 1];

  const summary =
    `Payloads launched and reentered per month, ${monthYear(months[0].month)} to ` +
    `${monthYear(months[months.length - 1].month)}. Launches peaked at ${fmtInt(peak.launched)} ` +
    `in ${monthYear(peak.month)}.` +
    (partial ? ` ${monthYear(partial.month)} is partial.` : "");

  return (
    <Panel title="Launch tempo" meta="payloads per month">
      <p className="story__lead">
        {full.length === 12
          ? `${fmtInt(launched12)} payloads were launched in the 12 months to ${monthYear(
              lastFull.month,
            )}, and ${fmtInt(reentered12)} came back down.`
          : `${fmtInt(launched12)} payloads were launched in the months shown, and ${fmtInt(
              reentered12,
            )} came back down.`}
      </p>
      <div className="legend" aria-hidden="true">
        <span className="legend__item">
          <span className="legend__swatch legend__swatch--signal" />
          launched
        </span>
        <span className="legend__item">
          <span className="legend__swatch legend__swatch--muted" />
          reentered
        </span>
        {partial ? (
          <span className="legend__item">
            <span className="legend__swatch legend__swatch--hatch" />
            {monthAbbr(partial.month)} so far
          </span>
        ) : null}
      </div>
      <div ref={ref} className="story-chart">
        {W > 0 ? (
          <svg width={W} height={H} role="img" aria-label={summary}>
            <defs>
              <pattern
                id={hatch}
                width={5}
                height={5}
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width={5} height={5} fill="var(--signal-wash)" />
                <line x1={0} y1={0} x2={0} y2={5} stroke="var(--signal)" strokeWidth={2} />
              </pattern>
            </defs>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={mL} x2={W - mR} y1={y(t)} y2={y(t)} className="story-chart__grid" />
                <text x={mL - 6} y={y(t) + 3.5} textAnchor="end">
                  {compactTick(t)}
                </text>
              </g>
            ))}
            {months.map((m, i) => {
              const x0 = mL + i * band + (band - (2 * barW + 2)) / 2;
              const hl = y(0) - y(m.launched);
              const hr = y(0) - y(m.reentered);
              const showLabel = (months.length - 1 - i) % labelEvery === 0;
              const isJan = m.month.endsWith("-01") || i === 0;
              const isLastFull = lastFull && m.month === lastFull.month;
              return (
                <g key={m.month}>
                  {m.launched > 0 ? (
                    <path
                      d={colPath(x0, y(m.launched), barW, hl)}
                      fill={m.partial ? `url(#${hatch})` : "var(--signal)"}
                    />
                  ) : null}
                  {m.reentered > 0 ? (
                    <path
                      d={colPath(x0 + barW + 2, y(m.reentered), barW, hr)}
                      fill="var(--ink-faint)"
                      opacity={m.partial ? 0.55 : 1}
                    />
                  ) : null}
                  {isLastFull ? (
                    <>
                      {/* the reentered value only where the band leaves it room */}
                      <text
                        x={x0 + barW / 2}
                        y={y(m.launched) - 5}
                        textAnchor="middle"
                        className="story-chart__value"
                      >
                        {fmtInt(m.launched)}
                      </text>
                      {band >= 34 ? (
                        <text
                          x={x0 + barW * 1.5 + 2}
                          y={y(m.reentered) - 5}
                          textAnchor="middle"
                          className="story-chart__value"
                        >
                          {fmtInt(m.reentered)}
                        </text>
                      ) : null}
                    </>
                  ) : null}
                  {showLabel ? (
                    <text x={mL + i * band + band / 2} y={H - mB + 15} textAnchor="middle">
                      {monthAbbr(m.month)}
                    </text>
                  ) : null}
                  {showLabel && isJan ? (
                    <text
                      x={mL + i * band + band / 2}
                      y={H - mB + 29}
                      textAnchor="middle"
                      className="story-chart__year"
                    >
                      {m.month.slice(0, 4)}
                    </text>
                  ) : null}
                  <rect
                    x={mL + i * band}
                    y={mT}
                    width={band}
                    height={plotH}
                    fill="transparent"
                    className="story-chart__hit"
                  >
                    <title>
                      {`${monthYear(m.month)}${m.partial ? " (so far)" : ""}: ${fmtInt(
                        m.launched,
                      )} payloads launched, ${fmtInt(m.reentered)} reentered`}
                    </title>
                  </rect>
                </g>
              );
            })}
            <line x1={mL} x2={W - mR} y1={y(0)} y2={y(0)} className="story-chart__axis" />
          </svg>
        ) : null}
      </div>
      <table className="sr-only">
        <caption>Payloads launched and reentered per month</caption>
        <thead>
          <tr>
            <th scope="col">Month</th>
            <th scope="col">Launched</th>
            <th scope="col">Reentered</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month}>
              <th scope="row">
                {monthYear(m.month)}
                {m.partial ? " (so far)" : ""}
              </th>
              <td>{m.launched}</td>
              <td>{m.reentered}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint story__hint">
        Payloads only, counted by launch date and by reentry (decay) date in the resolved catalog.
        {partial ? ` ${monthYear(partial.month)} runs through ${longDate(s.as_of)}.` : ""}
      </p>
    </Panel>
  );
}

/* ---- b. Latest launches ---------------------------------------------------- */

export function LatestLaunches({ s }: { s: StoriesResponse }) {
  const rows = s.recent_launches;
  if (rows.length === 0) {
    return (
      <Panel title="Latest launches" meta="newest first">
        <p className="story__lead">No launch with a payload is cataloged in the last 90 days.</p>
      </Panel>
    );
  }
  const total = rows.reduce((n, r) => n + r.payloads, 0);
  const pending = rows.filter((r) => !r.owner_attributed).length;
  const oldest = rows[rows.length - 1].date;
  const newest = rows[0].date;
  const count = rows.length === 10 ? "ten" : String(rows.length);
  return (
    <Panel title="Latest launches" meta="newest first">
      <p className="story__lead">
        {`The ${count} newest launches carried ${plural(total, "payload")} between ${longDate(
          oldest,
          s.as_of,
        )} and ${longDate(newest, s.as_of)}.`}
        {pending === rows.length
          ? " All of them are still cataloged mainly under a country or placeholder owner rather than a company."
          : pending > 0
            ? ` ${fmtInt(pending)} of them are still cataloged mainly under a country or placeholder owner rather than a company.`
            : ""}
      </p>
      <ol className="launches">
        {rows.map((r) => (
          <li key={r.launch_key} className="launch">
            <span className="launch__date num">{shortDate(r.date)}</span>
            <span className="launch__body">
              <span className="launch__name">
                <Link to={`/resolver/${r.lead_satellite_id}`}>{r.lead_name}</Link>
                {r.payloads > 1 ? (
                  <span className="launch__more"> and {fmtInt(r.payloads - 1)} more</span>
                ) : null}
              </span>
              <span className="launch__owner">
                {operatorName(r.owner)}
                {r.owners > 1 ? ` and ${plural(r.owners - 1, "other owner")}` : ""}
                {!r.owner_attributed ? (
                  <span className="pending-mark" title="The owner is a country code until GCAT names the company">
                    awaiting company attribution
                  </span>
                ) : null}
              </span>
            </span>
            <span className="launch__n num" title={`${fmtInt(r.objects)} cataloged objects, ${fmtInt(r.payloads)} of them payloads`}>
              {fmtInt(r.payloads)}
              <span className="launch__unit">{r.payloads === 1 ? " payload" : " payloads"}</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="hint story__hint">
        A launch is every object sharing a COSPAR launch number. The lead is its first named
        payload; the owner is the most common current owner among its payloads, as cataloged.
      </p>
    </Panel>
  );
}

/* ---- c. Coming down -------------------------------------------------------- */

function lifetime(launched: string | null, down: string): string | null {
  if (!launched) return null;
  const days = daysBetween(launched, down);
  if (days < 365) return `${plural(days, "day")} up`;
  return `${(Math.round((days / 365.25) * 10) / 10).toFixed(1)} years up`;
}

export function ComingDown({ s }: { s: StoriesResponse }) {
  const r = s.reentries;
  const v1 = r.starlink_v1;
  const laterStarlink = Math.max(0, r.starlink - v1);
  const others = Math.max(0, r.payloads - r.starlink);
  const segs = [
    { key: "v1", n: v1, label: "Starlink V1.0", cls: "seg--signal" },
    { key: "sl", n: laterStarlink, label: "later Starlink", cls: "seg--dim" },
    { key: "other", n: others, label: "everything else", cls: "seg--muted" },
  ].filter((x) => x.n > 0);
  return (
    <Panel title="Coming down" meta={`reentries · ${r.window_days} days`}>
      <p className="story__lead">
        {r.payloads === 0
          ? `No payload reentered in the last ${r.window_days} days.`
          : `${plural(r.payloads, "payload")} reentered in the last ${r.window_days} days, and ${fmtInt(
              r.starlink,
            )} of them were Starlink satellites.`}
        {v1 > 0 ? ` ${fmtInt(v1)} were from the first generation, launched as V1.0.` : ""}
      </p>
      {r.payloads > 0 ? (
        <>
          <div
            className="stackbar"
            role="img"
            aria-label={segs.map((x) => `${x.label}: ${x.n}`).join(", ")}
          >
            {segs.map((x) => (
              <span
                key={x.key}
                className={`stackbar__seg ${x.cls}`}
                style={{ flexGrow: x.n }}
                title={`${x.label}: ${fmtInt(x.n)} of ${fmtInt(r.payloads)}`}
              />
            ))}
          </div>
          <div className="legend legend--under">
            {segs.map((x) => (
              <span className="legend__item" key={x.key}>
                <span className={`legend__swatch ${x.cls}`} aria-hidden="true" />
                {x.label} <span className="num mono-hi">{fmtInt(x.n)}</span>
              </span>
            ))}
          </div>
        </>
      ) : null}
      {r.notable.length > 0 ? (
        <>
          <h3 className="story__sub">The other recent reentries</h3>
          <ul className="downlist">
            {r.notable.map((n) => {
              const up = lifetime(n.launched, n.date);
              return (
                <li key={n.satellite_id} className="downlist__row">
                  <span className="downlist__name">
                    <Link to={`/resolver/${n.satellite_id}`}>{n.name}</Link>
                    <span className="downlist__owner">{operatorName(n.owner)}</span>
                  </span>
                  <span className="downlist__when num">
                    {shortDate(n.date)}
                    {up ? <span className="downlist__up">{up}</span> : null}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
      <p className="hint story__hint">
        Payload reentries by decay date. Starlink is a name match, and the first generation is
        every name that starts with &quot;Starlink V1.0&quot;, which is how GCAT names that cohort.
      </p>
    </Panel>
  );
}

/* ---- d. The constellation race -------------------------------------------- */

function MiniSeries({
  series,
  label,
}: {
  series: { month: string; in_orbit: number }[];
  label: string;
}) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const H = 70;
  const mT = 8;
  const mB = 4;
  const pad = 6;
  const max = Math.max(1, ...series.map((p) => p.in_orbit));
  const n = series.length;
  const x = (i: number) => pad + (n > 1 ? (i / (n - 1)) * (W - 2 * pad) : 0);
  const y = (v: number) => mT + (H - mT - mB) * (1 - v / max);
  const line = series.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.in_orbit)}`).join("");
  const area = n ? `${line}L${x(n - 1)},${y(0)}L${x(0)},${y(0)}Z` : "";
  const last = series[n - 1];
  const step = n > 1 ? (W - 2 * pad) / (n - 1) : W;
  return (
    <div ref={ref} className="race__chart">
      {W > 0 && n > 0 ? (
        <svg width={W} height={H} role="img" aria-label={label}>
          <line x1={0} x2={W} y1={y(0)} y2={y(0)} className="story-chart__axis" />
          <path d={area} fill="var(--signal)" opacity={0.1} />
          <path
            d={line}
            fill="none"
            stroke="var(--signal)"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          <circle
            cx={x(n - 1)}
            cy={y(last.in_orbit)}
            r={4}
            fill="var(--signal)"
            stroke="var(--surface)"
            strokeWidth={2}
          />
          {series.map((p, i) => (
            <rect
              key={p.month}
              x={x(i) - step / 2}
              y={0}
              width={step}
              height={H}
              fill="transparent"
              className="story-chart__hit"
            >
              <title>{`${monthYear(p.month)}: ${fmtInt(p.in_orbit)} in orbit`}</title>
            </rect>
          ))}
        </svg>
      ) : null}
    </div>
  );
}

export function ConstellationRace({ s }: { s: StoriesResponse }) {
  const a = s.attribution;
  const through = a.complete_through;
  const withEnd = s.constellations
    .map((c) => ({ c, end: c.series.length ? c.series[c.series.length - 1].in_orbit : 0 }))
    .sort((p, q) => q.end - p.end);
  const [first, second] = withEnd;
  const rest = withEnd.slice(1).sort((p, q) => q.c.added_12m - p.c.added_12m);
  const fastest = rest[0];
  const name = (key: string) => CONSTELLATION_NAMES[key]?.name ?? key;

  let lead = "Company attribution is incomplete for every month scanned, so no lines are drawn.";
  if (through && first) {
    const ratio = second && second.end > 0 ? Math.round(first.end / second.end) : null;
    lead =
      `${name(first.c.key)} had ${fmtInt(first.end)} payloads in orbit at the end of ${monthYear(
        through,
      )}` +
      (ratio && ratio >= 2 && second
        ? `, ${fmtInt(ratio)} times as many as ${name(second.c.key)}, the next largest.`
        : ".") +
      (fastest && fastest.c.added_12m > 0
        ? ` Of the others, ${name(fastest.c.key)} added the most: ${plural(
            fastest.c.added_12m,
            "payload",
          )} launched in the 12 months to ${monthYear(through)}.`
        : "");
  }

  return (
    <Panel title="The constellation race" meta="payloads in orbit · month-end">
      <p className="story__lead">{lead}</p>
      <div className="race">
        {s.constellations.map((c) => {
          const names = CONSTELLATION_NAMES[c.key] ?? { name: c.key, owner: c.operator };
          const end = c.series.length ? c.series[c.series.length - 1] : null;
          const start = c.series[0];
          return (
            <div key={c.key} className="race__card">
              <div className="race__head">
                <span className="race__name">{names.name}</span>
                <span className="race__owner">{names.owner}</span>
              </div>
              <span className="race__value num">{end ? fmtInt(end.in_orbit) : "no data"}</span>
              <span className="race__sub">
                {end
                  ? `in orbit, end of ${monthAbbr(end.month)} ${end.month.slice(0, 4)}`
                  : "no attributed month yet"}
              </span>
              {end ? (
                <span className="race__sub">
                  <span className="num mono-hi">+{fmtInt(c.added_12m)}</span> launched in 12
                  months
                </span>
              ) : null}
              <MiniSeries
                series={c.series}
                label={
                  start && end
                    ? `${names.name}: ${fmtInt(start.in_orbit)} in orbit at the end of ${monthYear(
                        start.month,
                      )}, ${fmtInt(end.in_orbit)} at the end of ${monthYear(end.month)}.`
                    : `${names.name}: no series`
                }
              />
              {start && end ? (
                <div className="race__axis num">
                  <span>
                    {monthAbbr(start.month)} {start.month.slice(2, 4)}
                  </span>
                  <span>
                    {monthAbbr(end.month)} {end.month.slice(2, 4)}
                  </span>
                </div>
              ) : null}
              <span className="race__today">
                today, with a company owner: <span className="num">{fmtInt(c.in_orbit_now)}</span>
              </span>
            </div>
          );
        })}
      </div>
      <p className="hint story__hint">
        Counted by current owner, so Starlink is every SpaceX payload and OneWeb includes
        Eutelsat&apos;s other satellites. Each chart has its own vertical scale, starting at zero.
        {through && a.since
          ? ` ${plural(a.pending_payloads, "payload")} launched since ${longDate(
              a.since,
              s.as_of,
            )} still carry only a country code as owner, so these lines stop at ${monthYear(
              through,
            )} and the counts for today leave those payloads out.`
          : ""}
      </p>
    </Panel>
  );
}

/* ---- e. Who flies the active fleet ----------------------------------------- */

export function FleetShare({ s }: { s: StoriesResponse }) {
  const f = s.active_fleet;
  const [lead, ...next] = f.top;
  const nextSum = next.reduce((n, t) => n + t.n, 0);
  const pct = (n: number) => (f.total ? (100 * n) / f.total : 0);
  const rows = [
    ...f.top.map((t) => ({ key: t.operator, name: operatorName(t.operator), n: t.n, cls: "" })),
    { key: "country", name: "Owner cataloged only as a country", n: f.country_only, cls: "share__fill--hatch" },
    { key: "other", name: "Everyone else", n: f.other, cls: "share__fill--muted" },
  ];
  return (
    <Panel title="Who flies the active fleet" meta="active payloads by owner">
      <p className="story__lead">
        {lead
          ? `${operatorName(lead.operator)} operates ${fmtPct(lead.pct)} of the ${fmtInt(
              f.total,
            )} active payloads.` +
            (next.length
              ? ` The next ${next.length === 5 ? "five" : fmtInt(next.length)} operators together run ${fmtInt(nextSum)}.`
              : "")
          : "No active payloads are on record."}
      </p>
      <div className="share">
        {rows.map((r) => (
          <div key={r.key} className="share__row" title={`${r.name}: ${fmtInt(r.n)} active payloads`}>
            <span className="share__name">{r.name}</span>
            <span className="share__val num">
              {fmtInt(r.n)}
              <span className="share__pct"> · {fmtPct(pct(r.n))}</span>
            </span>
            <span className="share__track" aria-hidden="true">
              <span
                className={`share__fill ${r.cls}`}
                style={{ width: `${Math.max(0.4, pct(r.n))}%` }}
              />
            </span>
          </div>
        ))}
      </div>
      <p className="hint story__hint">
        Payloads whose latest status is ACTIVE, by current owner. The country-only row holds
        owners that are a country code, including recent launches still awaiting company
        attribution.
      </p>
    </Panel>
  );
}

/* ---- f. Space weather headline --------------------------------------------- */

export function weatherLead(s: StoriesResponse, env: EnvironmentResponse | null): string {
  const parts = [stormSentence(s)];
  const w = env?.worst_drag_day;
  if (w && w.median_dsma_m !== null && w.median_dsma_m < 0) {
    parts.push(
      `The worst drag day of the last 60 days was ${longDate(w.day, s.as_of)}, when the median LEO orbit sank ${fmtInt(
        Math.round(Math.abs(w.median_dsma_m)),
      )} meters in one day.`,
    );
  }
  return parts.join(" ");
}
