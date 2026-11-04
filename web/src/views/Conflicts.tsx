import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  getConflictsDecay,
  getConflictsStale,
  getConflictsStatus,
  getStats,
} from "../api/client";
import type {
  DecayConflictRow,
  Paginated,
  StaleOwnerRow,
  StatusConflictRow,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { fmtDate, fmtInt } from "../lib/format";
import { CONFLICT_TABS, toConflictTab, type ConflictTab } from "../lib/conflicts";
import { Panel } from "../components/Panel";
import { Cell, DataTable, Pager, type Column } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { Async, EmptyState } from "../components/States";

const LIMIT = 50;

/** One page of a tab's list, tagged with the tab it answers for (see the useApi below). */
type ConflictPage = Paginated<StatusConflictRow | DecayConflictRow | StaleOwnerRow> & {
  tab: ConflictTab;
};

export function Conflicts() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const tab = toConflictTab(params.get("tab"));
  // One page offset per tab, so a tab change never first asks for the old page.
  const [offsets, setOffsets] = useState<Record<string, number>>({});
  const offset = offsets[tab] ?? 0;
  const setOffset = (next: number) => setOffsets((o) => ({ ...o, [tab]: next }));

  const stats = useApi(() => getStats(), []);
  // keep: a page change dims the table in place instead of unmounting it with the pager;
  // keyed by the tab, so a tab change shows the loader, not the old tab's rows under new headers.
  // Each page still carries the tab it answers for: the columns and the pager below read
  // data.tab, so a kept page is never paired with another tab's columns, not even for the render
  // between the tab change and the hook dropping it.
  const page = useApi<ConflictPage>(
    () => {
      const rows: Promise<Paginated<StatusConflictRow | DecayConflictRow | StaleOwnerRow>> =
        tab === "status"
          ? getConflictsStatus(LIMIT, offset)
          : tab === "decay"
            ? getConflictsDecay(LIMIT, offset)
            : getConflictsStale(LIMIT, offset);
      return rows.then((d) => ({ ...d, tab }));
    },
    [tab, offset],
    { keep: true, keepKey: tab },
  );

  const open = (satelliteId: number) => navigate(`/resolver/${satelliteId}`);
  const counts = stats.data?.conflicts;

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Conflicts</h1>
          <p className="vhead__desc">
            The disagreements the resolver had to adjudicate — straight from the data-quality
            report. Every row deep-links into the resolver so you can read the underlying claims.
          </p>
        </div>
      </header>

      <Panel title="Cross-source conflicts" flush>
        <div className="tabs">
          {CONFLICT_TABS.map((t) => {
            const count = counts?.[t.statsKey];
            return (
              <button
                key={t.key}
                type="button"
                className={`tab${tab === t.key ? " is-active" : ""}`}
                aria-pressed={tab === t.key}
                onClick={() => setParams({ tab: t.key })}
              >
                {t.tabLabel}
                {count !== undefined ? <span className="tab__count num">{fmtInt(count)}</span> : null}
              </button>
            );
          })}
        </div>

        <p className="conflict-headline">
          {CONFLICT_TABS.find((t) => t.key === tab)?.headline}
        </p>

        <Async state={page} loadingLabel="Loading conflicts" loadingRows={10}>
          {(data) => (
            <>
              {data.rows.length === 0 && data.total > 0 ? (
                // a page past the end: the list shrank since it was counted
                <EmptyState
                  title="Nothing on this page"
                  message="The list changed since it was counted. Go back a page."
                />
              ) : data.tab === "status" ? (
                <DataTable<StatusConflictRow>
                  columns={STATUS_COLS}
                  rows={data.rows as StatusConflictRow[]}
                  rowKey={(r) => r.satellite_id}
                  onRowClick={(r) => open(r.satellite_id)}
                />
              ) : data.tab === "decay" ? (
                <DataTable<DecayConflictRow>
                  columns={DECAY_COLS}
                  rows={data.rows as DecayConflictRow[]}
                  rowKey={(r) => r.satellite_id}
                  onRowClick={(r) => open(r.satellite_id)}
                />
              ) : (
                <DataTable<StaleOwnerRow>
                  columns={STALE_COLS}
                  rows={data.rows as StaleOwnerRow[]}
                  rowKey={(r) => r.satellite_id}
                  onRowClick={(r) => open(r.satellite_id)}
                />
              )}
              <Pager
                offset={offsets[data.tab] ?? 0}
                limit={LIMIT}
                total={data.total}
                shown={page.loading ? undefined : data.rows.length}
                onOffset={setOffset}
              />
            </>
          )}
        </Async>
      </Panel>
    </div>
  );
}

const noradCell = (norad: number | null) => (
  <span className="num mono-hi">
    <Cell>{norad}</Cell>
  </span>
);

const STATUS_COLS: Column<StatusConflictRow>[] = [
  { key: "norad", header: "NORAD", num: true, render: (r) => noradCell(r.norad_id) },
  { key: "name", header: "Object", render: (r) => <span className="mono-hi">{r.canonical_name}</span> },
  { key: "satcat", header: "SATCAT", render: (r) => <StatusBadge status={r.satcat_status} /> },
  { key: "gcat", header: "GCAT", render: (r) => <StatusBadge status={r.gcat_status} /> },
];

const DECAY_COLS: Column<DecayConflictRow>[] = [
  { key: "norad", header: "NORAD", num: true, render: (r) => noradCell(r.norad_id) },
  { key: "name", header: "Object", render: (r) => <span className="mono-hi">{r.canonical_name}</span> },
  {
    key: "dates",
    header: "Sources and dates",
    render: (r) => <span className="wrap-cell">{r.sources_and_dates}</span>,
  },
];

const STALE_COLS: Column<StaleOwnerRow>[] = [
  { key: "norad", header: "NORAD", num: true, render: (r) => noradCell(r.norad_id) },
  { key: "name", header: "Object", render: (r) => <span className="mono-hi">{r.canonical_name}</span> },
  { key: "catalog", header: "Catalog owner", render: (r) => r.catalog_owner },
  { key: "resolved", header: "Resolves to", render: (r) => <span className="mono-hi">{r.resolved_operator}</span> },
  {
    key: "acq",
    header: "Acquired by",
    render: (r) => (
      <span>
        <span className="acq-arrow">⤳</span> <span className="mono-hi">{r.acquired_by}</span>
      </span>
    ),
  },
  { key: "date", header: "On", num: true, render: (r) => <span className="num">{fmtDate(r.acquisition_date)}</span> },
];
