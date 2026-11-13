import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  Cell as RCell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getCongestion, getOperator, getOperators } from "../api/client";
import type {
  OperatorCongestion,
  OperatorDetail,
  OperatorRow,
  OperatorSort,
  OperatorsResponse,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { SITE_NAME, useDocumentTitle } from "../seo";
import { compact, fmtDate, fmtInt, fmtRangeEnd } from "../lib/format";
import { REGIME_ORDER } from "../lib/status";
import { Panel } from "../components/Panel";
import { Cell, DataTable, Pager, pastTheEnd, type Column, type SortSpec } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { CongestionHeatmap } from "../components/CongestionHeatmap";
import { Async, EmptyState, ErrorState, Loading } from "../components/States";

const LIMIT = 100;

const STATUS_FILL: Record<string, string> = {
  ACTIVE: "#35d07a",
  PARTIAL: "#4bb8c9",
  SPARE: "#8595b0",
  INACTIVE: "#78828f",
  GRAVEYARD: "#9a8ce0",
  DECAYED: "#c06a54",
  UNKNOWN: "#626d7e",
};
// regime is an ordinal (altitude-ordered) bucket -> one-hue ramp, light rises with altitude.
// NO_ELEMENTS (owned sats lacking a current element set) is off-ramp -> a neutral slate so the
// chart accounts for the whole fleet and its shares don't overstate the classified regimes.
const REGIME_FILL: Record<string, string> = {
  LEO: "#1c5688",
  MEO: "#2472b3",
  GEO: "#3a92dd",
  HEO: "#62b4ff",
  NO_ELEMENTS: "#5a6472",
};

/** The league's meta line. A fleet counts every cataloged object an operator owns, reentered
    ones included, so the line says how many still have one in orbit when the API reports it. */
export function leagueMeta(sort: OperatorSort, data: OperatorsResponse | null): string {
  if (!data || data.with_fleet === undefined) return `sorted by ${sort}`;
  const owners =
    `${fmtInt(data.with_fleet)} of ${fmtInt(data.total)} operators own at least one ` +
    "cataloged object";
  return data.with_on_orbit === undefined
    ? `sorted by ${sort} · ${owners}.`
    : `sorted by ${sort} · ${owners}, and ${fmtInt(data.with_on_orbit)} of them still have one in orbit.`;
}

export function Operators() {
  const { operatorId } = useParams<{ operatorId: string }>();
  const navigate = useNavigate();

  const [sort, setSort] = useState<OperatorSort>("fleet");
  const [offset, setOffset] = useState(0);
  // keep: a sort or page change dims the table in place instead of unmounting it with the
  // sort button or pager that was just activated
  const league = useApi(() => getOperators(LIMIT, offset, sort), [sort, offset], { keep: true });
  const congestion = useApi(() => getCongestion(), []);

  // A non-numeric param (e.g. /operators/foo) must not fire GET /api/operators/NaN; treat it as
  // "not found" (idNum null) rather than issuing a bad request.
  const parsed = operatorId ? Number(operatorId) : null;
  const idNum = parsed !== null && Number.isFinite(parsed) ? parsed : null;
  const detail = useApi<OperatorDetail | null>(
    () => (idNum !== null ? getOperator(idNum) : Promise.resolve(null)),
    [idNum],
  );
  const opName = detail.data?.operator.canonical_name;
  useDocumentTitle(opName ? `${opName} · Operators · ${SITE_NAME}` : undefined);

  const sortSpec: SortSpec = { key: `sort:${sort}`, dir: sort === "name" ? "asc" : "desc" };
  const onSort = (key: string) => {
    const next = key.replace("sort:", "") as OperatorSort;
    setSort(next);
    setOffset(0);
  };

  const columns: Column<OperatorRow>[] = [
    {
      key: "sort:name",
      header: "Operator",
      sortable: true,
      render: (r) => (
        <span>
          <span className="mono-hi">{r.canonical_name}</span>
          {r.parent_name ? <span className="muted"> ⤳ {r.parent_name}</span> : null}
        </span>
      ),
    },
    { key: "country", header: "Country", render: (r) => <Cell>{r.country}</Cell> },
    { key: "class", header: "Class", render: (r) => <Cell>{r.operator_class}</Cell> },
    { key: "sort:fleet", header: "Fleet", num: true, sortable: true, render: (r) => fmtInt(r.fleet_total) },
    { key: "onorbit", header: "On-orbit", num: true, render: (r) => fmtInt(r.fleet_on_orbit) },
    { key: "sort:active", header: "Active", num: true, sortable: true, render: (r) => fmtInt(r.fleet_active) },
  ];

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Operators</h1>
          <p className="vhead__desc">
            The MSO league table over the resolved ownership graph. Select an operator to read its
            hierarchy, fleet mix, and the acquisitions that reshaped it.
          </p>
        </div>
      </header>

      <Panel title="Operator league" meta={leagueMeta(sort, league.data)} flush>
        <Async state={league} loadingLabel="Loading operators" loadingRows={10}>
          {(data) => (
            <>
              {pastTheEnd(data.rows.length, data.total) ? (
                // a page past the end: the list shrank since it was counted
                <EmptyState
                  title="Nothing on this page"
                  message="The list changed since it was counted. Go back a page."
                />
              ) : (
                <DataTable<OperatorRow>
                  columns={columns}
                  rows={data.rows}
                  rowKey={(r) => r.operator_id}
                  onRowClick={(r) => navigate(`/operators/${r.operator_id}`)}
                  sort={sortSpec}
                  onSort={onSort}
                  zebra
                />
              )}
              <Pager
                offset={offset}
                limit={LIMIT}
                total={data.total}
                shown={league.loading ? undefined : data.rows.length}
                onOffset={setOffset}
              />
            </>
          )}
        </Async>
      </Panel>

      {operatorId === undefined ? null : idNum === null ? (
        <EmptyState title="Operator not found" message={`No operator with id “${operatorId}”.`} />
      ) : detail.error ? (
        <ErrorState message={detail.error} onRetry={detail.reload} />
      ) : detail.loading && detail.data === null ? (
        <Loading label="Loading operator" />
      ) : detail.data ? (
        <OperatorDetailPanel detail={detail.data} />
      ) : null}

      <Panel title="Orbital congestion" meta="full field · 0–2000 km">
        <Async state={congestion} loadingLabel="Loading congestion field">
          {(c) => <CongestionHeatmap bins={c.bins} maxAltKm={2000} />}
        </Async>
        <p className="hint">
          Each shell counts the objects that have not reentered, by altitude and inclination, from
          the latest element set of each one. Debris and spent rocket bodies are almost entirely
          missing from the element feed, which is mostly CelesTrak&apos;s list of active satellites.
        </p>
      </Panel>
    </div>
  );
}

export function OperatorDetailPanel({ detail }: { detail: OperatorDetail }) {
  const o = detail.operator;
  const statusData = Object.entries(detail.fleet_by_status).map(([name, value]) => ({ name, value }));
  // Ordered four regimes first, then any off-ramp bucket (e.g. NO_ELEMENTS) so the shares
  // sum over the true fleet rather than just the classified objects.
  const regimeNames = [
    ...REGIME_ORDER.filter((r) => r in detail.fleet_by_regime),
    ...Object.keys(detail.fleet_by_regime).filter((r) => !REGIME_ORDER.includes(r)),
  ];
  const regimeData = regimeNames.map((name) => ({
    name,
    value: detail.fleet_by_regime[name],
  }));

  return (
    <div className="stack">
      <div className="idhead">
        <div>
          <h2 className="idhead__name">{o.canonical_name}</h2>
          <div className="idhead__badges">
            {o.country ? <span className="badge">{o.country}</span> : null}
            {o.operator_class ? <span className="badge">{o.operator_class}</span> : null}
            <span className="hint">operator #{o.operator_id}</span>
          </div>
        </div>
        <div className="hierarchy">
          <div className="hierarchy__col">
            <span className="label">Parents</span>
            {detail.parents.length === 0 ? (
              <span className="dash">—</span>
            ) : (
              detail.parents.map((p) => (
                <Link key={p.operator_id} to={`/operators/${p.operator_id}`} className="op-chip">
                  ↑ {p.canonical_name}
                </Link>
              ))
            )}
          </div>
          <div className="hierarchy__col">
            <span className="label">Children</span>
            {detail.children.length === 0 ? (
              <span className="dash">—</span>
            ) : (
              detail.children.map((c) => (
                <Link key={c.operator_id} to={`/operators/${c.operator_id}`} className="op-chip">
                  ↓ {c.canonical_name}
                </Link>
              ))
            )}
          </div>
        </div>
      </div>

      <div className="grid grid--2">
        <Panel title="Fleet by status" meta="current resolved state">
          {statusData.length === 0 ? (
            <EmptyState title="No fleet data" />
          ) : (
            <MiniBar data={statusData} colorFor={(n) => STATUS_FILL[n] ?? "#626d7e"} unit="objects" />
          )}
        </Panel>
        <Panel title="Fleet by regime" meta="orbital shell">
          {regimeData.length === 0 ? (
            <EmptyState title="No regime data" />
          ) : (
            <MiniBar data={regimeData} colorFor={(n) => REGIME_FILL[n] ?? "#2472b3"} unit="objects" />
          )}
        </Panel>
      </div>

      {detail.congestion === undefined ? null : (
        <Panel title="Congestion exposure" meta="shared altitude and inclination shells">
          {detail.congestion === null ? (
            <EmptyState
              title="None of its objects is in the congestion field"
              message={
                detail.operator.fleet_total === 0
                  ? "No object is on record in its name."
                  : "The field holds the latest element set of each object that has not reentered, and each of its objects has either reentered or has no element set on record."
              }
            />
          ) : (
            <CongestionExposure c={detail.congestion} />
          )}
        </Panel>
      )}

      <div className="grid grid--2">
        <Panel
          title="Acquisition history"
          meta="Only curated mergers and acquisitions are listed."
          flush
        >
          {detail.acquisitions.length === 0 ? (
            <EmptyState
              title="No curated merger or acquisition on record"
              message={
                detail.parents.length > 0 || detail.children.length > 0
                  ? "The parent and child links above show how these organizations are related, but they do not record whether a link began as an acquisition."
                  : undefined
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="dtable">
                <thead>
                  <tr>
                    <th>Counterparty</th>
                    <th>Relationship</th>
                    <th>From</th>
                    <th>To</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.acquisitions.map((a, i) => (
                    <tr key={i}>
                      <td className="mono-hi">
                        {a.child ? `↓ ${a.child}` : a.parent ? `↑ ${a.parent}` : "—"}
                      </td>
                      <td>{a.relationship}</td>
                      <td className="num">{fmtDate(a.valid_from)}</td>
                      <td className="num">{fmtRangeEnd(a.valid_to)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title="Fleet sample" meta="top objects" flush>
          {detail.top_satellites.length === 0 ? (
            <EmptyState title="No satellites" />
          ) : (
            <ul className="results">
              {detail.top_satellites.map((s) => (
                <li key={s.satellite_id}>
                  <Link className="result-row" to={`/resolver/${s.satellite_id}`}>
                    <span className="result-row__name">{s.canonical_name}</span>
                    <span className="result-row__meta">
                      <span className="num">{s.norad_id ?? "—"}</span>
                    </span>
                    <StatusBadge status={s.canonical_status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

/** Rounds non-negative values to whole units that add up to `target`: each is floored, and
    the units left over go to the largest remainders, never to a value that is 0. */
function largestRemainder(values: number[], target: number): number[] {
  const units = values.map(Math.floor);
  let spare = target - units.reduce((a, b) => a + b, 0);
  const byRemainder = values
    .map((_, i) => i)
    .filter((i) => values[i] > 0)
    .sort((i, j) => values[j] - units[j] - (values[i] - units[i]) || i - j);
  for (const i of byRemainder) {
    if (spare <= 0) break;
    units[i] += 1;
    spare -= 1;
  }
  return units;
}

/** The split of the average a reader sees: its own part, the other operators' part and the
    part with no owner on record, rounded by largest remainder so that the parts shown add up to
    the whole shown and a part that is truly 0 never gets a unit. The total is shown in whole
    numbers from 10 up (and always for a lone object, whose counts are exact) and to one decimal
    below. Below 1 a tenth of an object is too coarse to split, so the parts are given as
    proportions of the total, in tenths ("4 in 10 of them its own"). `full` is what the parts add
    up to: the total, or 10 for proportions. */
export function shareParts(c: OperatorCongestion): {
  total: number;
  full: number;
  own: number;
  others: number;
  unattributed: number;
  proportional: boolean;
  /** The total as shown. */
  totalText: string;
  /** Rounds and floors a value to the precision of an average shown. */
  round: (n: number) => number;
  floor: (n: number) => number;
  /** Formats a part. */
  text: (n: number) => string;
} {
  const n = c.neighbours_per_satellite;
  const raw = [
    c.own_per_satellite,
    c.others_per_satellite - c.unattributed_per_satellite,
    c.unattributed_per_satellite,
  ].map((v) => (v > 1e-9 ? v : 0));
  const whole = c.fleet_in_field === 1 || Math.round(n * 10) / 10 >= 10;
  const scale = whole ? 1 : 10;
  const total = Math.round(n * scale) / scale;
  const proportional = !whole && total > 0 && total < 1;
  const units = proportional
    ? largestRemainder(raw.map((v) => (v / n) * 10), 10)
    : largestRemainder(raw.map((v) => v * scale), Math.round(n * scale));
  const [own, others, unattributed] = proportional ? units : units.map((u) => u / scale);
  return {
    total: proportional ? total : units.reduce((a, b) => a + b, 0) / scale,
    full: proportional ? 10 : units.reduce((a, b) => a + b, 0) / scale,
    own,
    others,
    unattributed,
    proportional,
    totalText: whole ? fmtInt(total) : total.toFixed(1),
    round: (v: number) => Math.round(v * scale) / scale,
    floor: (v: number) => Math.floor(v * scale + 1e-9) / scale,
    text: (v: number) => (proportional ? `${v} in 10` : whole ? fmtInt(v) : v.toFixed(1)),
  };
}

function listed(parts: string[]): string {
  return parts.length === 1
    ? parts[0]
    : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The panel's sentences, separated from the markup so the wording is testable. "All" and
    "none" are said only of a part that is truly 0, not of one that rounds to 0. */
export function congestionSentences(c: OperatorCongestion): {
  where: string;
  share: string;
  main: string;
} {
  const alone = c.fleet_in_field === 1;
  const objects = alone ? "1 of its objects is" : `${fmtInt(c.fleet_in_field)} of its objects are`;
  const shells = c.shells === 1 ? "1 shell" : `${fmtInt(c.shells)} shells`;
  const where = `${objects} in the field, in ${shells} of 50 km by 5 degrees.`;

  let share: string;
  const p = shareParts(c);
  if (c.neighbours_per_satellite <= 1e-9) {
    share = alone ? "It has its shell to itself." : "Each one has its shell to itself.";
  } else if (alone) {
    // One object: the counts are exact, and none of the others can be its own.
    const other = p.others === 1 ? "1 belonging to another operator" : `${fmtInt(p.others)} belonging to other operators`;
    if (p.total === 1) {
      share = `It shares its shell with 1 other tracked object, which ${
        p.others === 1 ? "belongs to another operator" : "has no owner on record"
      }.`;
    } else {
      const parts =
        p.unattributed === 0
          ? "all of them belonging to other operators"
          : p.others === 0
            ? "all of them with no owner on record"
            : `${other} and ${fmtInt(p.unattributed)} with no owner on record`;
      share = `It shares its shell with ${fmtInt(p.total)} other tracked objects, ${parts}.`;
    }
  } else if (p.total === 0) {
    // Below 0.05 on average, fewer than 1 in 20 of its objects can share a shell at all.
    share = "Fewer than 1 in 20 of them shares its shell with another tracked object.";
  } else {
    // A part that takes the whole is "all" only when the other two are truly 0, and "nearly
    // all" otherwise, never "10 in 10" or a number equal to the total.
    const labels = ["its own", "belonging to other operators", "with no owner on record"];
    const raw = [
      c.own_per_satellite,
      c.others_per_satellite - c.unattributed_per_satellite,
      c.unattributed_per_satellite,
    ];
    const whole = [p.own, p.others, p.unattributed].findIndex((v) => v === p.full);
    const parts =
      whole >= 0
        ? [
            `${raw.every((v, i) => i === whole || v <= 1e-9) ? "all" : "nearly all"} of them ${labels[whole]}`,
          ]
        : [
            p.own > 0
              ? `${p.text(p.own)} of them its own`
              : c.own_per_satellite > 1e-9
                ? "almost none of them its own"
                : "none of them its own",
            ...(p.others > 0 ? [`${p.text(p.others)} belonging to other operators`] : []),
            ...(p.unattributed > 0 ? [`${p.text(p.unattributed)} with no owner on record`] : []),
          ];
    share = `Each one shares its shell with about ${p.totalText} other tracked objects on average, ${listed(parts)}.`;
  }

  const b = c.main_shell;
  const shell =
    `from ${fmtInt(b.alt_km_from)} to ${fmtInt(b.alt_km_to)} km at ` +
    `${b.inc_deg_from} to ${b.inc_deg_to} degrees of inclination`;
  const rest = b.objects - b.operator_objects;
  const withRest = `${fmtInt(rest)} ${rest === 1 ? "other object" : "other objects"}`;
  // The shell is picked by the most of its own objects and then by the most objects in all, so
  // among shells tied on the first count the one picked is the most crowded of them.
  const crowded = (of: string) =>
    b.tied_crowded_shells > 1 ? `One of the most crowded of ${of} is` : `The most crowded of ${of} is`;
  let main: string;
  if (alone) {
    main =
      `Its shell is ${shell}, ` +
      (rest === 0 ? "and it has the shell to itself." : `and it shares that shell with ${withRest}.`);
  } else if (b.operator_objects === 1 && rest === 0) {
    // Every object alone in its shell: the split already says so, and no shell stands out.
    main = "";
  } else if (b.operator_objects === 1) {
    main =
      `No shell holds more than one of its objects. ${crowded("its shells")} ${shell}, ` +
      (rest === 0
        ? "where its object has the shell to itself."
        : `where its object shares the shell with ${withRest}.`);
  } else {
    const lead =
      b.tied_shells > 1
        ? `${fmtInt(b.tied_shells)} shells each hold ${fmtInt(b.operator_objects)} of its objects, and none holds more. ${crowded("them")} ${shell}, where `
        : `The shell holding the most of its objects is ${shell}, where `;
    main =
      lead +
      `${fmtInt(b.operator_objects)} of its objects ` +
      (rest === 0 ? "have the shell to themselves." : `share the shell with ${withRest}.`);
  }
  return { where, share, main };
}

/** The other operators named under the split, each with its share at the precision of an
    average shown: only when the split shows the other operators a part, and only those whose
    share does not round to 0. Rounded shares that would add up to more than that part are
    rounded down instead, which never exceeds it and keeps equal shares equal. Below a total of
    1 the split is in proportions, and the names are given without numbers. */
export function namedNeighbours(
  c: OperatorCongestion,
): { operator_id: number; canonical_name: string; shown: string | null }[] {
  const p = shareParts(c);
  if (p.others === 0) return [];
  const named = (shown: (v: number) => string | null, keep: (v: number) => boolean) =>
    c.top_neighbours
      .filter((n) => keep(n.per_satellite))
      .map((n) => ({ operator_id: n.operator_id, canonical_name: n.canonical_name, shown: shown(n.per_satellite) }));
  if (p.proportional) return named(() => null, (v) => v > 1e-9);
  const rounded = c.top_neighbours.map((n) => p.round(n.per_satellite));
  const fit = rounded.reduce((a, b) => a + b, 0) <= p.others + 1e-9 ? p.round : p.floor;
  return named((v) => p.text(fit(v)), (v) => fit(v) > 0);
}

function CongestionExposure({ c }: { c: OperatorCongestion }) {
  const s = congestionSentences(c);
  const top = namedNeighbours(c);
  return (
    <div className="stack">
      <p className="story__lead">
        {s.where} {s.share}
      </p>
      {top.length > 0 ? (
        <p>
          Of those belonging to other operators, the largest{" "}
          {top.length === 1 ? "number comes" : "numbers come"} from{" "}
          {top.map((n, i) => (
            <span key={n.operator_id}>
              {i === 0 ? "" : i === top.length - 1 ? " and " : ", "}
              <Link to={`/operators/${n.operator_id}`}>{n.canonical_name}</Link>
              {n.shown === null ? null : (
                <>
                  {" "}(<span className="num">{n.shown}</span>
                  {i === 0 ? " per object" : ""})
                </>
              )}
            </span>
          ))}
          .
        </p>
      ) : null}
      {s.main ? <p>{s.main}</p> : null}
      <p className="hint">
        A shell is a band 50 km deep and 5 degrees of inclination wide that circles the whole
        Earth, so two objects in one shell can be on opposite sides of the planet: this measures
        density, not collision risk. The field is the latest element set of each object that has
        not reentered, mostly CelesTrak&apos;s list of active satellites, so debris and spent
        rocket bodies are almost entirely missing from it.
      </p>
    </div>
  );
}

interface BarDatum {
  name: string;
  value: number;
}

function MiniBar({
  data,
  colorFor,
  unit,
}: {
  data: BarDatum[];
  colorFor: (name: string) => string;
  unit: string;
}) {
  const total = data.reduce((acc, d) => acc + d.value, 0);
  return (
    <div className="minibar-wrap">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart layout="vertical" data={data} margin={{ top: 2, right: 16, bottom: 2, left: 6 }} barCategoryGap={6}>
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="name"
            width={78}
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10 }}
          />
          <Tooltip
            cursor={{ fill: "rgba(77,166,255,0.08)" }}
            content={({ active, payload }) => {
              if (!active || !payload || payload.length === 0) return null;
              const d = payload[0].payload as BarDatum;
              const pct = total > 0 ? ((d.value / total) * 100).toFixed(1) : "0.0";
              return (
                <div className="hm-tip" style={{ position: "static" }}>
                  <div>
                    <span className="hm-tip__k">{d.name} </span>
                    <span className="hm-tip__v">
                      {compact(d.value)} {unit}
                    </span>
                  </div>
                  <div>
                    <span className="hm-tip__k">share </span>
                    <span className="hm-tip__v">{pct}%</span>
                  </div>
                </div>
              );
            }}
          />
          <Bar dataKey="value" radius={[0, 3, 3, 0]} maxBarSize={16} isAnimationActive={false}>
            {data.map((d) => (
              <RCell key={d.name} fill={colorFor(d.name)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
