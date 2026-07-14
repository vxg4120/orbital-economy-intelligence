import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getSatellite, getSatelliteTrack, searchSatellites } from "../api/client";
import type { Assertion, Identifier, SatelliteDetail } from "../api/types";
import { useApi } from "../hooks/useApi";
import { fmtDate, fmtDateTime, fmtInt, fmtNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { StatusBadge } from "../components/StatusBadge";
import { SourceBadge } from "../components/SourceBadge";
import { OwnershipTimeline } from "../components/OwnershipTimeline";
import { LifeTrack } from "../components/LifeTrack";
import { Async, EmptyState, ErrorState, Loading } from "../components/States";

const EXAMPLES = [
  { id: 45440, label: "OneWeb L3-015 · SCD2 transfer" },
  { id: 23728, label: "Enhanced CRYSTAL 2105 · status conflict" },
  { id: 25544, label: "ISS (ZARYA)" },
  { id: 900001, label: "Analyst object · no NORAD" },
];

function groupBySource<T extends { source: string }>(items: T[]): [string, T[]][] {
  const map = new Map<string, T[]>();
  for (const it of items) {
    const arr = map.get(it.source) ?? [];
    arr.push(it);
    map.set(it.source, arr);
  }
  return Array.from(map.entries());
}

function groupByAttribute(items: Assertion[]): [string, Assertion[]][] {
  const map = new Map<string, Assertion[]>();
  for (const it of items) {
    const arr = map.get(it.attribute) ?? [];
    arr.push(it);
    map.set(it.attribute, arr);
  }
  return Array.from(map.entries());
}

export function Resolver() {
  const { satelliteId } = useParams<{ satelliteId: string }>();
  const navigate = useNavigate();

  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 180);
    return () => clearTimeout(t);
  }, [query]);

  const search = useApi(
    () => (debounced.trim() ? searchSatellites(debounced) : Promise.resolve({ results: [] })),
    [debounced],
  );

  // A non-numeric param (e.g. /resolver/foo) must not fire GET /api/satellites/NaN; treat it as
  // "not found" (idNum null) while a missing param stays "no selection" (see gate below).
  const parsed = satelliteId ? Number(satelliteId) : null;
  const idNum = parsed !== null && Number.isFinite(parsed) ? parsed : null;
  const detail = useApi<SatelliteDetail | null>(
    () => (idNum !== null ? getSatellite(idNum) : Promise.resolve(null)),
    [idNum],
  );

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Resolver</h1>
          <p className="vhead__desc">
            Search the resolved catalog, then read one object's full identity: every source's
            crosswalk, its temporal ownership, and where the sources disagree.
          </p>
        </div>
      </header>

      <div className="searchbar">
        <span className="searchbar__prompt" aria-hidden="true">
          ⌕
        </span>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name, NORAD id, or COSPAR (e.g. Starlink, 25544, 1998-067A)"
          aria-label="Search satellites"
          autoComplete="off"
          spellCheck={false}
        />
      </div>

      <div className="resolver-grid">
        <Panel
          title="Results"
          meta={search.data ? `${fmtInt(search.data.results.length)} hits` : ""}
          flush
        >
          <ResultsList
            debounced={debounced}
            loading={search.loading}
            error={search.error}
            results={search.data?.results ?? []}
            activeId={idNum}
            onPick={(id) => navigate(`/resolver/${id}`)}
          />
        </Panel>

        <div className="resolver-detail">
          {satelliteId === undefined ? (
            <EmptyState
              title="Select an object"
              message="Pick a search result to open its identity card."
            />
          ) : idNum === null ? (
            <EmptyState title="Not found" message={`No object with id “${satelliteId}”.`} />
          ) : detail.error ? (
            <ErrorState message={detail.error} onRetry={detail.reload} />
          ) : detail.loading && detail.data === null ? (
            <Loading label="Resolving object" />
          ) : detail.data ? (
            <IdentityCard detail={detail.data} />
          ) : (
            <EmptyState title="Not found" message={`No object with id ${idNum}.`} />
          )}
        </div>
      </div>
    </div>
  );
}

function ResultsList({
  debounced,
  loading,
  error,
  results,
  activeId,
  onPick,
}: {
  debounced: string;
  loading: boolean;
  error: string | null;
  results: { satellite_id: number; norad_id: number | null; canonical_name: string; canonical_status: string | null; operator_name: string | null }[];
  activeId: number | null;
  onPick: (id: number) => void;
}) {
  if (!debounced.trim()) {
    return (
      <div className="results-empty">
        <span className="label">Try an example</span>
        <ul className="example-list">
          {EXAMPLES.map((e) => (
            <li key={e.id}>
              <Link to={`/resolver/${e.id}`}>{e.label}</Link>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (error) return <ErrorState message={error} />;
  if (loading) return <Loading label="Searching" />;
  if (results.length === 0)
    return <EmptyState title="No matches" message={`Nothing matches “${debounced}”.`} />;

  return (
    <ul className="results">
      {results.map((r) => (
        <li key={r.satellite_id}>
          <button
            className={`result-row${activeId === r.satellite_id ? " is-active" : ""}`}
            onClick={() => onPick(r.satellite_id)}
          >
            <span className="result-row__name">{r.canonical_name}</span>
            <span className="result-row__meta">
              <span className="num">{r.norad_id !== null ? r.norad_id : "—"}</span>
              <span className="muted"> · {r.operator_name ?? "unattributed"}</span>
            </span>
            <StatusBadge status={r.canonical_status} />
          </button>
        </li>
      ))}
    </ul>
  );
}

function IdentityCard({ detail }: { detail: SatelliteDetail }) {
  const s = detail.satellite;
  const conflicts = new Set(detail.conflicts);
  const crosswalk = useMemo(() => groupBySource<Identifier>(detail.identifiers), [detail.identifiers]);
  const assertionGroups = useMemo(() => groupByAttribute(detail.assertions), [detail.assertions]);

  return (
    <div className="idcard stack">
      <div className="idhead">
        <div>
          <h2 className="idhead__name">{s.canonical_name}</h2>
          <div className="idhead__badges">
            <StatusBadge status={s.canonical_status} />
            <span className="badge">{s.object_type}</span>
            {conflicts.size > 0 ? (
              <span className="badge badge--conflict">
                <span className="badge__glyph" aria-hidden="true" />
                {conflicts.size} conflicting {conflicts.size === 1 ? "attribute" : "attributes"}
              </span>
            ) : null}
          </div>
        </div>
        <div className="kv">
          <div className="kv__item">
            <span className="kv__k">NORAD</span>
            <span className="kv__v">{s.norad_id !== null ? s.norad_id : <span className="dash">—</span>}</span>
          </div>
          <div className="kv__item">
            <span className="kv__k">COSPAR</span>
            <span className="kv__v">{s.cospar_id ?? <span className="dash">—</span>}</span>
          </div>
          <div className="kv__item">
            <span className="kv__k">Operator</span>
            <span className="kv__v">{s.operator_name ?? <span className="dash">—</span>}</span>
          </div>
          <div className="kv__item">
            <span className="kv__k">Launch</span>
            <span className="kv__v">{fmtDate(s.launch_date)}</span>
          </div>
          <div className="kv__item">
            <span className="kv__k">Decay</span>
            <span className="kv__v">{fmtDate(s.decay_date)}</span>
          </div>
          <div className="kv__item">
            <span className="kv__k">Object id</span>
            <span className="kv__v">{s.satellite_id}</span>
          </div>
        </div>
      </div>

      <Panel title="Identifier crosswalk" meta={`${detail.identifiers.length} rows · by source`} flush>
        {crosswalk.length === 0 ? (
          <EmptyState title="No identifiers" />
        ) : (
          <div>
            {crosswalk.map(([source, ids]) => (
              <div className="crosswalk-src" key={source}>
                <div className="crosswalk-src__head">
                  <SourceBadge source={source} />
                  <span className="hint">{ids.length} ids</span>
                </div>
                {ids.map((id, i) => (
                  <div className="idpair" key={`${id.id_type}-${i}`}>
                    <span className="idpair__type">{id.id_type}</span>
                    <span className="idpair__val">{id.id_value}</span>
                    <span className="idpair__conf num">conf {fmtNum(id.confidence, 2)}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Ownership timeline" meta="SCD type-2 · role segments">
        <OwnershipTimeline segments={detail.ownership} />
      </Panel>

      <Panel title="Life track" meta="daily orbit history · sma + perigee/apogee">
        <LifeTrackSection satelliteId={s.satellite_id} />
      </Panel>

      <div className="grid grid--2">
        <Panel title="Source assertions" meta="raw claims, pre-resolution" flush>
          {assertionGroups.length === 0 ? (
            <EmptyState title="No assertions" />
          ) : (
            <div className="assert-list">
              {assertionGroups.map(([attr, rows]) => {
                const isConflict = conflicts.has(attr);
                return (
                  <div className={`assert-group${isConflict ? " is-conflict" : ""}`} key={attr}>
                    <div className="assert-group__head">
                      <span className="assert-attr">{attr}</span>
                      {isConflict ? (
                        <span className="badge badge--conflict">
                          <span className="badge__glyph" aria-hidden="true" />
                          conflict
                        </span>
                      ) : null}
                    </div>
                    {rows.map((a, i) => (
                      <div className="assert-line" key={`${a.source}-${i}`}>
                        <SourceBadge source={a.source} />
                        <span className="assert-val">{a.value}</span>
                        <span className="assert-when num">{fmtDate(a.observed_at)}</span>
                      </div>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </Panel>

        <Panel title="Status history" meta="latest first" flush>
          {detail.status_history.length === 0 ? (
            <EmptyState title="No status history" />
          ) : (
            <div className="assert-list">
              {detail.status_history.map((h, i) => (
                <div className="assert-line" key={`${h.source}-${i}`}>
                  <StatusBadge status={h.canonical_status} />
                  <span className="assert-val muted">{fmtDateTime(h.observed_at)}</span>
                  <SourceBadge source={h.source} />
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Latest orbit" meta="most recent GP element set">
        {detail.latest_elements ? (
          <div className="orbit">
            <OrbitCell k="Epoch" v={fmtDateTime(detail.latest_elements.epoch)} />
            <OrbitCell k="SMA (km)" v={fmtNum(detail.latest_elements.semi_major_axis_km, 1)} />
            <OrbitCell k="Apogee (km)" v={fmtNum(detail.latest_elements.apogee_km, 1)} />
            <OrbitCell k="Perigee (km)" v={fmtNum(detail.latest_elements.perigee_km, 1)} />
            <OrbitCell k="Inclination" v={`${fmtNum(detail.latest_elements.inclination, 3)}°`} />
            <OrbitCell k="Ecc" v={fmtNum(detail.latest_elements.eccentricity, 5)} />
            <OrbitCell k="Mean motion" v={fmtNum(detail.latest_elements.mean_motion, 4)} />
          </div>
        ) : (
          <EmptyState
            title="No element set"
            message="No GP elements on record — common for GEO, classified, or pre-catalog objects."
          />
        )}
      </Panel>

      <Panel title="Merge audit" meta="no silent merges">
        {detail.merge_events.length === 0 ? (
          <span className="hint">No recorded merge events for this object.</span>
        ) : (
          <div className="stack stack--sm">
            {detail.merge_events.map((m, i) => (
              <div className="footnote" key={i}>
                Rule <span className="mono-hi">{m.rule_fired}</span>
                {m.score !== null ? (
                  <>
                    {" "}
                    · score <span className="num">{fmtNum(m.score, 3)}</span>
                  </>
                ) : null}{" "}
                · <span className="num">{fmtDateTime(m.merged_at)}</span>
                {m.details ? (
                  <span className="muted"> · {JSON.stringify(m.details)}</span>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

function OrbitCell({ k, v }: { k: string; v: string }) {
  return (
    <div className="orbit__cell">
      <div className="orbit__k">{k}</div>
      <div className="orbit__v">{v}</div>
    </div>
  );
}

/** Fetches and renders the full LifeTrack for one object. A null-norad or history-less object
    resolves to an empty series and LifeTrack shows its own "no element-set history" state. */
function LifeTrackSection({ satelliteId }: { satelliteId: number }) {
  const track = useApi(() => getSatelliteTrack(satelliteId), [satelliteId]);
  return (
    <Async state={track} loadingLabel="Loading track">
      {(t) => <LifeTrack data={t} variant="full" />}
    </Async>
  );
}
