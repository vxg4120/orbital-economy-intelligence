import { Link } from "react-router-dom";
import {
  getAuditSummary,
  getCongestion,
  getEnvironment,
  getStats,
  getStories,
} from "../api/client";
import { useApi } from "../hooks/useApi";
import { fmtInt, fmtPct } from "../lib/format";
import { CONFLICT_TABS } from "../lib/conflicts";
import { dispatch, longDate, operatorName, shortDate } from "../lib/stories";
import { Panel } from "../components/Panel";
import { StatTile } from "../components/StatTile";
import { CoverageMeter } from "../components/CoverageMeter";
import { LedgerTable } from "../components/LedgerTable";
import { CongestionHeatmap } from "../components/CongestionHeatmap";
import { EnvironmentStrip } from "../components/EnvironmentStrip";
import { AuditStrip } from "../components/AuditStrip";
import {
  ComingDown,
  ConstellationRace,
  FleetShare,
  LatestLaunches,
  LaunchTempo,
  weatherLead,
} from "../components/Stories";
import { Async, Loading } from "../components/States";

export function Overview() {
  const stories = useApi(() => getStories(), []);
  const stats = useApi(() => getStats(), []);
  const audit = useApi(() => getAuditSummary(), []);
  const congestion = useApi(() => getCongestion(), []);
  const env = useApi(() => getEnvironment(60), []);
  const s = stories.data;

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">What&apos;s happening in orbit</h1>
          {s ? (
            <>
              <p className="dispatch">{dispatch(s).join(" ")}</p>
              <p className="dispatch__asof">
                As of <span className="num">{longDate(s.as_of)}, {s.as_of.slice(0, 4)}</span>,
                from the latest successful ingest.
              </p>
            </>
          ) : stories.loading ? (
            <Loading label="Reading the latest dispatch" rows={2} />
          ) : null}
        </div>
      </header>

      <Async state={stories} loadingLabel="Loading the stories" loadingRows={10}>
        {(st) => {
          const spacex = st.active_fleet.top[0];
          const a = st.attribution;
          return (
            <>
              <div className="grid grid--stats" data-tour="stats">
                <StatTile
                  lead
                  hero
                  label="Payloads launched"
                  n={st.last_30d.payloads_launched}
                  sub={
                    <>
                      in 30 days, on <span className="num">{fmtInt(st.last_30d.launches)}</span>{" "}
                      launches
                    </>
                  }
                />
                <StatTile
                  label="Payloads reentered"
                  n={st.last_30d.payloads_reentered}
                  sub={
                    <>
                      in 30 days, <span className="num">{fmtInt(st.reentries.starlink)}</span> of
                      them Starlink
                    </>
                  }
                />
                <StatTile
                  label="Active payloads"
                  n={st.active_fleet.total}
                  sub={
                    spacex ? (
                      <>
                        {operatorName(spacex.operator)} operates{" "}
                        <span className="num">{fmtPct(spacex.pct)}</span>
                      </>
                    ) : (
                      "latest status ACTIVE"
                    )
                  }
                />
                <StatTile
                  label="Awaiting attribution"
                  n={a.pending_payloads}
                  sub={
                    a.since ? (
                      <>
                        payloads launched since{" "}
                        <span className="num">{shortDate(a.since)}</span> with only a country as
                        owner
                      </>
                    ) : (
                      "payloads with only a country as owner"
                    )
                  }
                />
              </div>

              <div className="grid grid--2 stories-row">
                <LaunchTempo s={st} />
                <FleetShare s={st} />
              </div>

              <ConstellationRace s={st} />

              <div className="grid grid--2 stories-row">
                <LatestLaunches s={st} />
                <ComingDown s={st} />
              </div>
            </>
          );
        }}
      </Async>

      <Panel title="Space weather" meta="drag environment · 60 days">
        {s ? <p className="story__lead">{weatherLead(s, env.data)}</p> : null}
        <Async state={env} loadingLabel="Loading drag environment">
          {(e) => <EnvironmentStrip env={e} />}
        </Async>
        <p className="hint story__hint">
          Bars show the fleet-wide median one-day change in semi-major axis over consecutive-day
          LEO element pairs. The median is robust to maneuvers, so when a bar stretches, the
          atmosphere moved everyone. Storm days on the NOAA G scale are tinted. Indices come from
          CelesTrak&apos;s consolidated space-weather data.
        </p>
      </Panel>

      <Async state={audit} loadingLabel="Loading audit">
        {(a) => <AuditStrip summary={a} />}
      </Async>

      <header className="underhood">
        <h2 className="underhood__title">Under the hood</h2>
        <p className="underhood__desc">
          A read-only window on the identity graph: one resolved object per physical satellite,
          stitched from SATCAT, GCAT and UCS. These are its coverage and the disagreements the
          resolver had to adjudicate.
        </p>
      </header>

      <Async state={stats} loadingLabel="Loading telemetry" loadingRows={12}>
        {(s) => (
          <>
            <div className="grid grid--stats">
              <StatTile
                lead
                label="Tracked objects"
                n={s.satellites}
                sub={
                  <>
                    on-orbit payloads <span className="num">{fmtInt(s.on_orbit_payloads)}</span>
                  </>
                }
              />
              <StatTile
                label="Operators"
                n={s.operators}
                sub={
                  <>
                    crosswalk rows <span className="num">{fmtInt(s.identifier_rows)}</span>
                  </>
                }
              />
              <StatTile
                label="Element sets"
                n={s.gp_elements}
                sub="latest GP per object"
              />
              <StatTile
                label="Merge events"
                n={s.merge_events}
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
