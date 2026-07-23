import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  getBus,
  getBusHistory,
  getBusMethodology,
  getBusProvenance,
  getBuses,
} from "../api/client";
import type {
  BusDetail,
  BusGroup,
  BusMethodology,
  BusRow,
  BusSort,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { fmtDate, fmtInt, fmtNum, fmtPct } from "../lib/format";
import { Panel } from "../components/Panel";
import { Cell, DataTable, Pager, type Column, type SortSpec } from "../components/DataTable";
import { StatTile } from "../components/StatTile";
import { CoverageMeter } from "../components/CoverageMeter";
import { StatusBadge } from "../components/StatusBadge";
import { Async, EmptyState, ErrorState, Loading } from "../components/States";

const LIMIT = 50;
const MIN_N_OPTIONS = [1, 5, 25, 100];

// Descending metric sorts; everything else (tto, station_keeping, name) ranks ascending.
const DESC_SORTS: BusSort[] = [
  "fleet", "on_orbit", "active", "sk_share", "decayed_share", "lifetime", "compliance", "coverage",
];

/** "n of m observed" caption for a behavior metric, so no number hides its denominator. */
function nOf(n: number, of: number): string {
  return `n ${fmtInt(n)} of ${fmtInt(of)}`;
}

export function Buses() {
  const { slug } = useParams<{ slug: string }>();
  const [search] = useSearchParams();
  const navigate = useNavigate();

  const [group, setGroup] = useState<BusGroup>("manufacturer");
  const [sort, setSort] = useState<BusSort>("fleet");
  const [minN, setMinN] = useState(5);
  const [offset, setOffset] = useState(0);

  const board = useApi(
    () => getBuses(group, sort, minN, LIMIT, offset),
    [group, sort, minN, offset],
  );
  const methodology = useApi(() => getBusMethodology(), []);

  const kindParam = search.get("kind");
  const kind: BusGroup | undefined =
    kindParam === "manufacturer" || kindParam === "bus" ? kindParam : undefined;
  const detail = useApi<BusDetail | null>(
    () => (slug ? getBus(slug, kind) : Promise.resolve(null)),
    [slug, kind],
  );

  const sortSpec: SortSpec = {
    key: `sort:${sort}`,
    dir: DESC_SORTS.includes(sort) ? "desc" : "asc",
  };
  const onSort = (key: string) => {
    setSort(key.replace("sort:", "") as BusSort);
    setOffset(0);
  };
  const pickGroup = (g: BusGroup) => {
    setGroup(g);
    setSort("fleet");
    setOffset(0);
  };

  const columns: Column<BusRow>[] = [
    {
      key: "sort:name",
      header: group === "manufacturer" ? "Manufacturer" : "Bus model",
      sortable: true,
      render: (r) => (
        <span>
          <span className="mono-hi">{r.name}</span>
          {group === "manufacturer" && r.manufacturer_country ? (
            <span className="muted"> · {r.manufacturer_country}</span>
          ) : null}
          {group === "bus" && r.primary_manufacturer ? (
            <span className="muted"> · {r.primary_manufacturer}</span>
          ) : null}
        </span>
      ),
    },
    { key: "sort:fleet", header: "Fleet", num: true, sortable: true, render: (r) => fmtInt(r.fleet_total) },
    { key: "sort:on_orbit", header: "On-orbit", num: true, sortable: true, render: (r) => fmtInt(r.fleet_on_orbit) },
    {
      key: "sort:tto",
      header: "TTO days",
      num: true,
      sortable: true,
      render: (r) => <Cell>{r.median_days_to_operational !== null ? fmtNum(r.median_days_to_operational, 0) : null}</Cell>,
    },
    {
      key: "sort:sk_share",
      header: "SK share",
      num: true,
      sortable: true,
      render: (r) => <Cell>{r.station_keeping_share_pct !== null ? fmtPct(r.station_keeping_share_pct) : null}</Cell>,
    },
    {
      key: "sort:station_keeping",
      header: "SK p50 km",
      num: true,
      sortable: true,
      render: (r) => <Cell>{r.p50_station_keeping_km !== null ? fmtNum(r.p50_station_keeping_km, 3) : null}</Cell>,
    },
    {
      key: "sort:decayed_share",
      header: "Decayed",
      num: true,
      sortable: true,
      render: (r) => <Cell>{r.decayed_share_pct !== null ? fmtPct(r.decayed_share_pct) : null}</Cell>,
    },
    {
      key: "sort:lifetime",
      header: "Life yrs",
      num: true,
      sortable: true,
      render: (r) => <Cell>{r.median_lifetime_years !== null ? fmtNum(r.median_lifetime_years, 1) : null}</Cell>,
    },
    {
      key: "sort:coverage",
      header: "GP cov",
      num: true,
      sortable: true,
      render: (r) => <Cell>{r.gp_coverage_pct !== null ? fmtPct(r.gp_coverage_pct) : null}</Cell>,
    },
  ];

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Bus Benchmarks</h1>
          <p className="vhead__desc">
            An independent, provenance-tracked scoreboard for spacecraft platforms: fleet,
            time-to-operational, station-keeping, lifetime and disposal, per manufacturer and per
            bus model. Behavior metrics come from GP element history and report their n; the
            methodology below states every definition and caveat.
          </p>
        </div>
      </header>

      <Panel
        title="Leaderboard"
        meta={
          <>
            {methodology.data ? `methodology v${methodology.data.version} · ` : ""}
            cohort n ≥ {minN}
          </>
        }
        flush
      >
        <div className="review-filters">
          <div className="tabs tabs--sub">
            <button
              className={`tab${group === "manufacturer" ? " is-active" : ""}`}
              onClick={() => pickGroup("manufacturer")}
            >
              Manufacturers
            </button>
            <button
              className={`tab${group === "bus" ? " is-active" : ""}`}
              onClick={() => pickGroup("bus")}
            >
              Bus models
            </button>
          </div>
          <div className="tabs tabs--sub" aria-label="Minimum cohort size">
            {MIN_N_OPTIONS.map((n) => (
              <button
                key={n}
                className={`tab${minN === n ? " is-active" : ""}`}
                onClick={() => {
                  setMinN(n);
                  setOffset(0);
                }}
              >
                n≥{n}
              </button>
            ))}
          </div>
        </div>
        <Async state={board} loadingLabel="Loading leaderboard">
          {(data) => (
            <>
              <DataTable<BusRow>
                columns={columns}
                rows={data.rows}
                rowKey={(r) => r.slug}
                onRowClick={(r) => navigate(`/buses/${r.slug}?kind=${group}`)}
                sort={sortSpec}
                onSort={onSort}
                zebra
              />
              <Pager offset={offset} limit={LIMIT} total={data.total} onOffset={setOffset} />
            </>
          )}
        </Async>
      </Panel>

      {slug === undefined ? null : detail.error ? (
        <ErrorState message={detail.error} onRetry={detail.reload} />
      ) : detail.loading && detail.data === null ? (
        <Loading label="Loading benchmark detail" />
      ) : detail.data ? (
        <BusDetailPanel detail={detail.data} slug={slug} />
      ) : null}

      <Async state={methodology} loadingLabel="Loading methodology">
        {(m) => <MethodologyPanel m={m} />}
      </Async>
    </div>
  );
}

function BusDetailPanel({ detail, slug }: { detail: BusDetail; slug: string }) {
  const b = detail.benchmark;
  const cov = detail.provenance.metric_coverage;
  const history = useApi(() => getBusHistory(slug, detail.kind), [slug, detail.kind]);

  return (
    <div className="stack">
      <div className="idhead">
        <div>
          <h2 className="idhead__name">{b.name}</h2>
          <div className="idhead__badges">
            <span className="badge">{detail.kind === "manufacturer" ? "manufacturer" : "bus model"}</span>
            {detail.kind === "manufacturer" && b.manufacturer_country ? (
              <span className="badge">{b.manufacturer_country}</span>
            ) : null}
            {detail.kind === "bus" && b.primary_manufacturer ? (
              <span className="badge">{b.primary_manufacturer}</span>
            ) : null}
            <span className="hint">
              slug {slug} · methodology v{detail.provenance.methodology_version}
            </span>
          </div>
        </div>
      </div>

      <div className="grid grid--stats">
        <StatTile
          lead
          label="Fleet"
          value={fmtInt(b.fleet_total)}
          sub={
            <>
              on-orbit <span className="num">{fmtInt(b.fleet_on_orbit)}</span> · active{" "}
              <span className="num">{fmtInt(b.fleet_active)}</span>
            </>
          }
        />
        <StatTile
          label="Time to operational"
          value={b.median_days_to_operational !== null ? `${fmtNum(b.median_days_to_operational, 0)} d` : "no data"}
          sub={nOf(b.tto_n, b.fleet_total)}
        />
        <StatTile
          label="Station-keeping share"
          value={b.station_keeping_share_pct !== null ? fmtPct(b.station_keeping_share_pct) : "no data"}
          sub={
            <>
              {nOf(b.sk_n, b.fleet_total)}
              {b.p50_station_keeping_km !== null ? (
                <>
                  {" "}· p50 <span className="num">{fmtNum(b.p50_station_keeping_km, 3)} km</span>
                </>
              ) : null}
            </>
          }
        />
        <StatTile
          label="Decayed share"
          value={b.decayed_share_pct !== null ? fmtPct(b.decayed_share_pct) : "0%"}
          sub={
            b.median_lifetime_years !== null ? (
              <>
                median life <span className="num">{fmtNum(b.median_lifetime_years, 1)} yrs</span>
              </>
            ) : (
              "no decayed cohort"
            )
          }
        />
      </div>

      <div className="grid grid--2">
        <Panel
          title={detail.kind === "manufacturer" ? "Bus models" : "Manufacturers"}
          meta="constituents"
          flush
        >
          {detail.constituents.length === 0 ? (
            <EmptyState title="No constituents attributed" />
          ) : (
            <div className="table-wrap">
              <table className="dtable dtable--zebra">
                <thead>
                  <tr>
                    <th>{detail.kind === "manufacturer" ? "Bus model" : "Manufacturer"}</th>
                    <th className="is-num">Fleet</th>
                    <th className="is-num">On-orbit</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.constituents.map((c) => (
                    <tr key={c.slug ?? "none"}>
                      <td>
                        {c.slug ? (
                          <Link
                            className="mono-hi"
                            to={`/buses/${c.slug}?kind=${detail.kind === "manufacturer" ? "bus" : "manufacturer"}`}
                          >
                            {c.name}
                          </Link>
                        ) : (
                          <Cell>{c.name}</Cell>
                        )}
                      </td>
                      <td className="is-num">{fmtInt(c.fleet_total)}</td>
                      <td className="is-num">{fmtInt(c.fleet_on_orbit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {detail.orgs.length > 0 ? (
            <div className="panel__body" style={{ borderTop: "1px solid var(--rule)" }}>
              <span className="label">Constituent GCAT orgs</span>
              <div className="srckey" style={{ marginTop: 6 }}>
                {detail.orgs.map((o) => (
                  <span key={o.code} className="chip" title={o.rollup_source ?? undefined}>
                    {o.code} · {o.org_name ?? o.code} · {fmtInt(o.fleet_total)}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </Panel>

        <Panel title="Fleet sample" meta="active and observed first" flush>
          {detail.satellites_sample.length === 0 ? (
            <EmptyState title="No satellites" />
          ) : (
            <ul className="results">
              {detail.satellites_sample.map((s) => (
                <li key={s.satellite_id}>
                  <Link className="result-row" to={`/resolver/${s.satellite_id}`}>
                    <span className="result-row__name">
                      {s.canonical_name}
                      {s.bus_uncertain || s.manufacturer_uncertain ? (
                        <span className="muted" title="GCAT marks this attribution uncertain"> ?</span>
                      ) : null}
                    </span>
                    <span className="result-row__meta">
                      <span className="num">{s.norad_id ?? "no norad"}</span>
                    </span>
                    <StatusBadge status={s.canonical_status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="grid grid--2">
        <Panel title="Coverage and provenance" meta={`source gcat · run #${detail.provenance.ingest_run_id ?? "?"}`}>
          <CoverageMeter
            label="GP behavior data"
            pct={b.fleet_total > 0 ? (100 * cov.gp_behavior.n) / b.fleet_total : 0}
            foot={nOf(cov.gp_behavior.n, b.fleet_total)}
          />
          <CoverageMeter
            label="Station-keeping observable"
            pct={b.fleet_total > 0 ? (100 * cov.station_keeping.n) / b.fleet_total : 0}
            foot={nOf(cov.station_keeping.n, b.fleet_total)}
          />
          <CoverageMeter
            label="Time-to-operational cohort"
            pct={b.fleet_total > 0 ? (100 * cov.time_to_operational.n) / b.fleet_total : 0}
            foot={nOf(cov.time_to_operational.n, b.fleet_total)}
          />
          <p className="hint" style={{ marginTop: 10 }}>
            Disposal verdicts: {fmtInt(cov.disposal.n)} decidable of {fmtInt(cov.disposal.of)}{" "}
            decayed. Uncertain attributions: {fmtInt(detail.provenance.uncertain_attributions)}.
            Attribution built {fmtDate(detail.provenance.built_at)}.
          </p>
          <p className="hint">
            Confirm or dispute this attribution: email vibhavgupta2@gmail.com with subject
            {" "}&quot;Bus attribution: {b.name}&quot;. Adjudicated corrections enter the record
            with provenance and outrank catalog sources.
          </p>
        </Panel>

        <Panel title="Monthly record" meta="immutable snapshots">
          <Async state={history} loadingLabel="Loading snapshots">
            {(h) =>
              h.snapshots.length === 0 ? (
                <EmptyState title="No snapshots yet" message="Captured on the first refresh of each month." />
              ) : (
                <div className="table-wrap">
                  <table className="dtable">
                    <thead>
                      <tr>
                        <th>Month</th>
                        <th className="is-num">Fleet</th>
                        <th className="is-num">On-orbit</th>
                        <th className="is-num">SK share</th>
                        <th>Methodology</th>
                      </tr>
                    </thead>
                    <tbody>
                      {h.snapshots.map((s) => (
                        <tr key={s.snapshot_month}>
                          <td className="num">{fmtDate(s.snapshot_month)}</td>
                          <td className="is-num">{fmtInt(Number(s.metrics.fleet_total))}</td>
                          <td className="is-num">{fmtInt(Number(s.metrics.fleet_on_orbit))}</td>
                          <td className="is-num">
                            <Cell>
                              {s.metrics.station_keeping_share_pct !== null
                                ? fmtPct(Number(s.metrics.station_keeping_share_pct))
                                : null}
                            </Cell>
                          </td>
                          <td>v{s.methodology_version}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Async>
        </Panel>
      </div>

      <ReceiptsPanel slug={slug} kind={detail.kind} />
    </div>
  );
}

const RECEIPT_METRICS: { key: string; label: string }[] = [
  { key: "fleet", label: "Fleet" },
  { key: "station_keeping", label: "Station-keeping" },
  { key: "tto", label: "Time to operational" },
  { key: "lifetime", label: "Lifetime" },
  { key: "coverage", label: "GP coverage" },
];

/** The receipts: per-satellite rows behind one headline metric, straight from the API. */
function ReceiptsPanel({ slug, kind }: { slug: string; kind: BusGroup }) {
  const [metric, setMetric] = useState("fleet");
  const [offset, setOffset] = useState(0);
  const limit = 15;
  const receipts = useApi(
    () => getBusProvenance(slug, metric, kind, limit, offset),
    [slug, kind, metric, offset],
  );

  return (
    <Panel
      title="Receipts"
      meta="every number traces to source rows"
      flush
    >
      <div className="review-filters">
        <div className="tabs tabs--sub">
          {RECEIPT_METRICS.map((m) => (
            <button
              key={m.key}
              className={`tab${metric === m.key ? " is-active" : ""}`}
              onClick={() => {
                setMetric(m.key);
                setOffset(0);
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <Async state={receipts} loadingLabel="Loading receipts">
        {(data) => (
          <>
            <div className="table-wrap">
              <table className="dtable dtable--zebra">
                <thead>
                  <tr>
                    <th>Satellite</th>
                    <th className="is-num">NORAD</th>
                    <th>Status</th>
                    <th className="is-num">Value</th>
                    <th>Source row</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.satellite_id}>
                      <td>
                        <Link className="mono-hi" to={`/resolver/${r.satellite_id}`}>
                          {r.canonical_name}
                        </Link>
                      </td>
                      <td className="is-num">
                        <Cell>{r.norad_id}</Cell>
                      </td>
                      <td>
                        <StatusBadge status={r.canonical_status} />
                      </td>
                      <td className="is-num">
                        <Cell>{r.value === null ? null : String(r.value)}</Cell>
                      </td>
                      <td>
                        <span className="hint">
                          {r.source} {r.source_key} · run #{r.ingest_run_id}
                          {r.bus_uncertain || r.manufacturer_uncertain ? " · uncertain" : ""}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="panel__body" style={{ paddingTop: 8, paddingBottom: 8 }}>
              <span className="hint">Cohort: {data.cohort}.</span>
            </div>
            <Pager offset={offset} limit={limit} total={data.total} onOffset={setOffset} />
          </>
        )}
      </Async>
    </Panel>
  );
}

function MethodologyPanel({ m }: { m: BusMethodology }) {
  return (
    <Panel
      title="Methodology"
      meta={
        <a href={m.doc_url} target="_blank" rel="noreferrer">
          v{m.version} · {m.updated_at} · full doc on GitHub →
        </a>
      }
    >
      <p className="hint" style={{ marginBottom: 10 }}>{m.purpose}</p>

      <div className="grid grid--2">
        <div>
          <span className="label">Metric definitions</span>
          <ul className="results" style={{ marginTop: 6 }}>
            {m.metrics.map((x) => (
              <li key={x.key} style={{ padding: "6px 0", borderBottom: "1px solid var(--rule)" }}>
                <span className="mono-hi">{x.label}</span>
                <span className="hint" style={{ display: "block" }}>{x.definition}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <span className="label">Known limitations</span>
          <ul className="results" style={{ marginTop: 6 }}>
            {m.limitations.map((x, i) => (
              <li key={i} style={{ padding: "6px 0", borderBottom: "1px solid var(--rule)" }}>
                <span className="hint">{x}</span>
              </li>
            ))}
          </ul>
          <span className="label" style={{ marginTop: 12, display: "block" }}>Provenance</span>
          <p className="hint">{m.provenance_guarantee}</p>
          <span className="label" style={{ marginTop: 12, display: "block" }}>Corrections</span>
          <p className="hint">{m.correction_channel}</p>
        </div>
      </div>

      <p className="hint" style={{ marginTop: 10 }}>
        Inclusion: {m.inclusion} Default cohort floor: n ≥ {m.cohort_minimum}. Refresh: {m.refresh}
      </p>
    </Panel>
  );
}
