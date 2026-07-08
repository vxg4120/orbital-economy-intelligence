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
import type { OperatorDetail, OperatorRow, OperatorSort } from "../api/types";
import { useApi } from "../hooks/useApi";
import { compact, fmtDate, fmtInt, fmtRangeEnd } from "../lib/format";
import { REGIME_ORDER } from "../lib/status";
import { Panel } from "../components/Panel";
import { Cell, DataTable, Pager, type Column, type SortSpec } from "../components/DataTable";
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

export function Operators() {
  const { operatorId } = useParams<{ operatorId: string }>();
  const navigate = useNavigate();

  const [sort, setSort] = useState<OperatorSort>("fleet");
  const [offset, setOffset] = useState(0);
  const league = useApi(() => getOperators(LIMIT, offset, sort), [sort, offset]);
  const congestion = useApi(() => getCongestion(), []);

  // A non-numeric param (e.g. /operators/foo) must not fire GET /api/operators/NaN; treat it as
  // "not found" (idNum null) rather than issuing a bad request.
  const parsed = operatorId ? Number(operatorId) : null;
  const idNum = parsed !== null && Number.isFinite(parsed) ? parsed : null;
  const detail = useApi<OperatorDetail | null>(
    () => (idNum !== null ? getOperator(idNum) : Promise.resolve(null)),
    [idNum],
  );

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

      <Panel title="Operator league" meta={`sorted by ${sort}`} flush>
        <Async state={league} loadingLabel="Loading operators">
          {(data) => (
            <>
              <DataTable<OperatorRow>
                columns={columns}
                rows={data.rows}
                rowKey={(r) => r.operator_id}
                onRowClick={(r) => navigate(`/operators/${r.operator_id}`)}
                sort={sortSpec}
                onSort={onSort}
                zebra
              />
              <Pager offset={offset} limit={LIMIT} total={data.total} onOffset={setOffset} />
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
      </Panel>
    </div>
  );
}

function OperatorDetailPanel({ detail }: { detail: OperatorDetail }) {
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

      <div className="grid grid--2">
        <Panel title="Acquisition history" meta="ownership relationships" flush>
          {detail.acquisitions.length === 0 ? (
            <EmptyState title="No acquisitions on record" />
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
