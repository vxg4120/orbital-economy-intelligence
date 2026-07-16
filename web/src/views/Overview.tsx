import { Link } from "react-router-dom";
import { getAuditSummary, getCongestion, getStats } from "../api/client";
import { useApi } from "../hooks/useApi";
import { compact, fmtInt } from "../lib/format";
import { CONFLICT_TABS } from "../lib/conflicts";
import { Panel } from "../components/Panel";
import { StatTile } from "../components/StatTile";
import { CoverageMeter } from "../components/CoverageMeter";
import { LedgerTable } from "../components/LedgerTable";
import { CongestionHeatmap } from "../components/CongestionHeatmap";
import { AuditStrip } from "../components/AuditStrip";
import { Async } from "../components/States";

export function Overview() {
  const stats = useApi(() => getStats(), []);
  const audit = useApi(() => getAuditSummary(), []);
  const congestion = useApi(() => getCongestion(), []);

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Overview</h1>
          <p className="vhead__desc">
            A read-only window on the identity graph — one resolved object per physical satellite,
            stitched from SATCAT, GCAT and UCS. The numbers below are the coverage and the
            disagreements the resolver had to adjudicate.
          </p>
        </div>
      </header>

      <Async state={stats} loadingLabel="Loading telemetry">
        {(s) => (
          <>
            <div className="grid grid--stats" data-tour="stats">
              <StatTile
                lead
                hero
                label="Tracked objects"
                value={compact(s.satellites)}
                sub={
                  <>
                    on-orbit payloads <span className="num">{fmtInt(s.on_orbit_payloads)}</span>
                  </>
                }
              />
              <StatTile
                label="Operators"
                value={compact(s.operators)}
                sub={
                  <>
                    crosswalk rows <span className="num">{fmtInt(s.identifier_rows)}</span>
                  </>
                }
              />
              <StatTile
                label="Element sets"
                value={compact(s.gp_elements)}
                sub="latest GP per object"
              />
              <StatTile
                label="Merge events"
                value={compact(s.merge_events)}
                sub="norad + cospar exact"
              />
            </div>

            <div className="grid grid--2" data-tour="conflicts">
              <Panel title="Resolution coverage" meta="on-orbit payloads">
                <CoverageMeter
                  label="Resolved current owner"
                  pct={s.coverage.operator_pct}
                  foot="role = owner, valid_to is null"
                />
                <CoverageMeter
                  label="Non-UNKNOWN status"
                  pct={s.coverage.status_pct}
                  foot="mapped to canonical taxonomy"
                />
                <CoverageMeter
                  label="≥2 source identifiers"
                  pct={s.coverage.multi_source_pct}
                  foot="cross-catalog corroboration"
                />
              </Panel>

              <Panel title="Open conflicts" meta="disagreements are data">
                <div className="conflict-list">
                  {CONFLICT_TABS.map((c) => (
                    <Link key={c.key} to={`/conflicts?tab=${c.key}`} className="conflict-row">
                      <span className="conflict-row__count num">
                        {fmtInt(s.conflicts[c.statsKey])}
                      </span>
                      <span className="conflict-row__body">
                        <span className="conflict-row__label">{c.cardLabel}</span>
                        <span className="conflict-row__sub">{c.cardSub}</span>
                      </span>
                      <span className="conflict-row__arrow" aria-hidden="true">
                        →
                      </span>
                    </Link>
                  ))}
                </div>
              </Panel>
            </div>

            <Async state={audit} loadingLabel="Loading audit">
              {(a) => <AuditStrip summary={a} />}
            </Async>

            <Panel title="Ingestion ledger" meta="last run per source · endpoint" flush>
              <LedgerTable runs={s.ingest_runs} />
            </Panel>
          </>
        )}
      </Async>

      <Panel
        title="Orbital congestion"
        meta={<Link to="/operators">full field →</Link>}
      >
        <Async state={congestion} loadingLabel="Loading congestion field">
          {(c) => (
            <CongestionHeatmap bins={c.bins} maxAltKm={1250} cellW={12} cellH={8} />
          )}
        </Async>
        <p className="hint" style={{ marginTop: 10 }}>
          LEO shell occupancy from the latest element set per object. Full 0–2000 km field on the{" "}
          <Link to="/operators">Operators</Link> view. Click a shell to read its count.
        </p>
      </Panel>
    </div>
  );
}
