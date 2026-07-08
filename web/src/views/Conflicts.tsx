import { useEffect, useState } from "react";
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
import { Panel } from "../components/Panel";
import { Cell, DataTable, Pager, type Column } from "../components/DataTable";
import { StatusBadge } from "../components/StatusBadge";
import { Async } from "../components/States";

type TabKey = "status" | "decay" | "stale";
const LIMIT = 50;

const TABS: { key: TabKey; label: string }[] = [
  { key: "status", label: "Status" },
  { key: "decay", label: "Decay dates" },
  { key: "stale", label: "Stale owners" },
];

const HEADLINE: Record<TabKey, string> = {
  status:
    "Objects where SATCAT and GCAT map to different canonical states — one source calls it active, the other reentered. Resolved by the deterministic ordering (observed_at, ingest_run, source_key).",
  decay:
    "Objects whose reentry date differs across sources once parsed to a real date, so “1957 Dec 1 1000?” and “1957-12-01” don't count. The raw claims stay visible.",
  stale:
    "Objects whose latest catalog owner still resolves to a company that has since been acquired — the graph knows the parent; the catalog still names the child.",
};

export function Conflicts() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const tab = (params.get("tab") as TabKey) || "status";
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [tab]);

  const stats = useApi(() => getStats(), []);
  const page = useApi<Paginated<StatusConflictRow | DecayConflictRow | StaleOwnerRow>>(() => {
    if (tab === "status") return getConflictsStatus(LIMIT, offset);
    if (tab === "decay") return getConflictsDecay(LIMIT, offset);
    return getConflictsStale(LIMIT, offset);
  }, [tab, offset]);

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
          {TABS.map((t) => {
            const count =
              t.key === "status"
                ? counts?.status
                : t.key === "decay"
                  ? counts?.decay
                  : counts?.stale_owners;
            return (
              <button
                key={t.key}
                className={`tab${tab === t.key ? " is-active" : ""}`}
                onClick={() => setParams({ tab: t.key })}
              >
                {t.label}
                {count !== undefined ? <span className="tab__count num">{fmtInt(count)}</span> : null}
              </button>
            );
          })}
        </div>

        <p className="conflict-headline">{HEADLINE[tab]}</p>

        <Async state={page} loadingLabel="Loading conflicts">
          {(data) => (
            <>
              {tab === "status" ? (
                <DataTable<StatusConflictRow>
                  columns={STATUS_COLS}
                  rows={data.rows as StatusConflictRow[]}
                  rowKey={(r) => r.satellite_id}
                  onRowClick={(r) => open(r.satellite_id)}
                />
              ) : tab === "decay" ? (
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
              <Pager offset={offset} limit={LIMIT} total={data.total} onOffset={setOffset} />
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
