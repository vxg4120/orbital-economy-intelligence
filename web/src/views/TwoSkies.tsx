import { useMemo, useState } from "react";
import {
  getCongestionAstronomy,
  getPasses,
  getTargets,
  SITES,
  type PassesResponse,
  type TwoSkyTarget,
} from "../api/twoskies";
import { useApi } from "../hooks/useApi";
import { compact, fmtInt } from "../lib/format";
import { Panel } from "../components/Panel";
import { StatTile } from "../components/StatTile";
import { CongestionHeatmap } from "../components/CongestionHeatmap";
import { DataTable, type Column } from "../components/DataTable";
import { Async, EmptyState, ErrorState, Loading } from "../components/States";

/** datetime-local default: 22:00 local tonight — a plausible "run it tonight" slot. */
function defaultWhen(): string {
  const d = new Date();
  d.setHours(22, 0, 0, 0);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(
    d.getMinutes(),
  )}`;
}

/** ISO "…Z" -> "HH:MM:SS" UTC. */
function hms(iso: string): string {
  const d = new Date(iso);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

interface PassState {
  loading: boolean;
  data: PassesResponse | null;
  error: string | null;
}

const PASS_COLUMNS: Column<PassesResponse["passes"][number]>[] = [
  { key: "norad", header: "NORAD", num: true, render: (r) => <span className="num">{r.norad}</span> },
  { key: "name", header: "Object", render: (r) => r.name },
  { key: "operator", header: "Operator", render: (r) => r.operator ?? <span className="dash">—</span> },
  {
    key: "sep",
    header: "Closest sep",
    num: true,
    render: (r) => <span className="num">{r.closest_sep_deg.toFixed(2)}°</span>,
  },
  { key: "alt", header: "Elev", num: true, render: (r) => <span className="num">{r.alt_deg.toFixed(0)}°</span> },
  { key: "alt_km", header: "Orbit", num: true, render: (r) => <span className="num">{fmtInt(r.alt_km)} km</span> },
  { key: "time", header: "Time UTC", num: true, render: (r) => <span className="num">{hms(r.time_utc)}</span> },
];

export function TwoSkies() {
  const congestion = useApi(() => getCongestionAstronomy(), []);
  const targets = useApi(() => getTargets(), []);

  const [selected, setSelected] = useState<number>(0);
  const [when, setWhen] = useState<string>(() => defaultWhen());
  const [site, setSite] = useState<string>("kitt_peak");
  const [windowMin, setWindowMin] = useState<number>(60);
  const [sepDeg, setSepDeg] = useState<number>(5);
  const [passes, setPasses] = useState<PassState>({ loading: false, data: null, error: null });

  const targetList = targets.data?.targets ?? [];
  const famous = useMemo(() => targetList.filter((t) => t.category === "famous"), [targetList]);
  const conflict = useMemo(() => targetList.filter((t) => t.category === "conflict"), [targetList]);
  const current: TwoSkyTarget | undefined = targetList[selected];

  function run() {
    if (!current) return;
    const iso = new Date(when).toISOString();
    setPasses({ loading: true, data: null, error: null });
    getPasses({ ra: current.ra_deg, dec: current.dec_deg, datetime: iso, site, window_min: windowMin, sep_deg: sepDeg })
      .then((data) => setPasses({ loading: false, data, error: null }))
      .catch((e: unknown) =>
        setPasses({ loading: false, data: null, error: e instanceof Error ? e.message : "Request failed" }),
      );
  }

  const optLabel = (t: TwoSkyTarget) =>
    `${t.host}${t.has_conflict ? "  ⚠ disputed" : ""}  ·  ${t.disposition.toLowerCase()}`;

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Two Skies</h1>
          <p className="vhead__desc">
            One view, two catalogs. The satellite identity graph knows where ~16k LEO objects are
            right now; the exoplanet graph knows where the interesting targets sit on the sky. This
            bridge asks the ground-based-astronomy question: when you point a telescope at an
            exoplanet host to confirm a TESS candidate, which satellites streak past its line of
            sight — and who owns them.
          </p>
        </div>
      </header>

      {/* ---------------- Panel A: the sky is getting crowded ---------------- */}
      <Panel title="The sky is getting crowded" meta="megaconstellations × astronomy">
        <p className="ts-lede">
          Ground-based follow-up and wide-field surveys now share the sky with an exploding LEO
          population. A single low-orbit shell can hold thousands of objects; sunlit satellites leave
          streaks across exposures and confuse difference-imaging pipelines. The{" "}
          <strong>IAU Centre for the Protection of the Dark and Quiet Sky from Satellite
          Constellation Interference (CPS)</strong>{" "}
          coordinates the community response. The density below is a catalog-density proxy, not
          conjunction data.
        </p>

        <Async state={congestion} loadingLabel="Loading congestion field">
          {(c) => {
            const spacex = c.top_operators.find((o) => /spacex/i.test(o.operator));
            const spacexPct = spacex ? Math.round((100 * spacex.payloads) / c.leo_objects) : null;
            const shellMax = Math.max(1, ...c.shells.map((s) => s.objects));
            return (
              <>
                <div className="grid grid--stats">
                  <StatTile
                    lead
                    hero
                    label="LEO tracked objects"
                    value={compact(c.leo_objects)}
                    sub={
                      <>
                        peak shell{" "}
                        <span className="num">{fmtInt(c.peak_bin.object_count)}</span> in one 50 km×5°
                        bin
                      </>
                    }
                  />
                  <StatTile
                    label="Payloads launched"
                    value={compact(c.payloads_launched_1y)}
                    sub={
                      <>
                        last year · <span className="num">{fmtInt(c.payloads_launched_30d)}</span> in 30
                        days
                      </>
                    }
                  />
                  <StatTile
                    label="Largest operator"
                    value={spacex ? compact(spacex.payloads) : "—"}
                    sub={
                      spacexPct !== null ? (
                        <>
                          SpaceX · ~<span className="num">{spacexPct}%</span> of LEO
                        </>
                      ) : (
                        "payloads on orbit"
                      )
                    }
                  />
                  <StatTile
                    label="Catalog objects"
                    value={compact(c.catalog_objects)}
                    sub={
                      <>
                        <span className="num">{compact(c.tracked_with_elements)}</span> with live
                        elements
                      </>
                    }
                  />
                </div>

                <div className="grid grid--2" style={{ marginTop: "var(--pad)" }}>
                  <div>
                    <span className="label">LEO shell occupancy</span>
                    <div className="ts-shells">
                      {c.shells
                        .filter((s) => s.alt_lo_km < 1400)
                        .map((s) => (
                          <div className="ts-shell" key={s.alt_lo_km}>
                            <span className="ts-shell__k num">
                              {s.alt_lo_km}–{s.alt_hi_km}
                            </span>
                            <span
                              className="ts-shell__bar"
                              style={{ width: `${(100 * s.objects) / shellMax}%` }}
                            />
                            <span className="ts-shell__v num">{fmtInt(s.objects)}</span>
                          </div>
                        ))}
                    </div>
                    <p className="hint" style={{ marginTop: 8 }}>
                      Objects per ~200 km altitude band (km). The occupied shells are where follow-up
                      exposures collect streaks.
                    </p>
                  </div>
                  <div>
                    <span className="label">Density by altitude × inclination</span>
                    <CongestionHeatmap bins={c.bins} maxAltKm={1250} cellW={12} cellH={8} />
                  </div>
                </div>
              </>
            );
          }}
        </Async>
      </Panel>

      {/* ---------------- Panel B: line of sight forecaster ---------------- */}
      <Panel title="Line of sight" meta="illustrative · ground-based follow-up">
        <div className="ts-caveat">
          <span className="ts-caveat__title">Read this first — honest caveats</span>
          <ul>
            <li>
              <strong>TESS itself is largely immune.</strong> It observes from a high lunar-resonant
              orbit far above LEO. This tool is for <strong>ground-based follow-up</strong> — the
              TFOP network and wide surveys that confirm TESS candidates from Earth.
            </li>
            <li>
              <strong>Positions are approximate.</strong> Element sets are up to ~1 week old, so
              propagated positions drift by arcminutes-to-degrees. Good for “roughly which objects
              cross this patch of sky,” not collision-grade.
            </li>
            <li>
              Only LEO payloads are propagated; frames are approximate (no precession/refraction). A
              pass near the line of sight is a plausible contaminant, not a certainty.
            </li>
          </ul>
        </div>

        <Async state={targets} loadingLabel="Loading targets">
          {() => (
            <>
              <div className="ts-form">
                <div className="ts-field ts-field--grow">
                  <label className="ts-field__label" htmlFor="ts-target">
                    Exoplanet target
                  </label>
                  <select
                    id="ts-target"
                    className="ts-select"
                    value={selected}
                    onChange={(e) => setSelected(Number(e.target.value))}
                  >
                    <optgroup label="Famous systems">
                      {famous.map((t) => (
                        <option key={t.host} value={targetList.indexOf(t)}>
                          {optLabel(t)}
                        </option>
                      ))}
                    </optgroup>
                    <optgroup label="Conflict-flagged (disputed disposition)">
                      {conflict.map((t) => (
                        <option key={t.host} value={targetList.indexOf(t)}>
                          {optLabel(t)}
                        </option>
                      ))}
                    </optgroup>
                  </select>
                </div>

                <div className="ts-field">
                  <label className="ts-field__label" htmlFor="ts-when">
                    Start (local)
                  </label>
                  <input
                    id="ts-when"
                    className="ts-datetime"
                    type="datetime-local"
                    value={when}
                    onChange={(e) => setWhen(e.target.value)}
                  />
                </div>

                <div className="ts-field">
                  <label className="ts-field__label" htmlFor="ts-site">
                    Observatory
                  </label>
                  <select
                    id="ts-site"
                    className="ts-select"
                    value={site}
                    onChange={(e) => setSite(e.target.value)}
                  >
                    {SITES.map((s) => (
                      <option key={s.key} value={s.key}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="ts-field">
                  <label className="ts-field__label" htmlFor="ts-window">
                    Window
                  </label>
                  <select
                    id="ts-window"
                    className="ts-select"
                    value={windowMin}
                    onChange={(e) => setWindowMin(Number(e.target.value))}
                  >
                    <option value={30}>30 min</option>
                    <option value={60}>60 min</option>
                    <option value={120}>120 min</option>
                    <option value={180}>180 min</option>
                  </select>
                </div>

                <div className="ts-field">
                  <label className="ts-field__label" htmlFor="ts-sep">
                    Threshold
                  </label>
                  <select
                    id="ts-sep"
                    className="ts-select"
                    value={sepDeg}
                    onChange={(e) => setSepDeg(Number(e.target.value))}
                  >
                    <option value={1}>1°</option>
                    <option value={2}>2°</option>
                    <option value={5}>5°</option>
                    <option value={10}>10°</option>
                  </select>
                </div>

                <button className="btn btn--submit" onClick={run} disabled={!current || passes.loading}>
                  {passes.loading ? "Propagating…" : "Run forecast"}
                </button>
              </div>

              {current ? (
                <p className="hint" style={{ marginTop: 8 }}>
                  {current.host} · RA <span className="num">{current.ra_deg.toFixed(3)}°</span> / Dec{" "}
                  <span className="num">{current.dec_deg.toFixed(3)}°</span>
                  {current.has_conflict ? (
                    <>
                      {" "}
                      · <span className="ts-conflict-dot">⚠ catalogs disagree on disposition</span>
                    </>
                  ) : null}
                </p>
              ) : null}

              <div style={{ marginTop: 14 }}>
                {passes.loading ? (
                  <Loading label="SGP4-propagating the LEO catalog" />
                ) : passes.error ? (
                  <ErrorState message={passes.error} onRetry={run} />
                ) : passes.data ? (
                  <PassesResult data={passes.data} />
                ) : (
                  <EmptyState
                    title="No forecast yet"
                    message="Pick a target, time and observatory, then run the forecast."
                  />
                )}
              </div>
            </>
          )}
        </Async>
      </Panel>
    </div>
  );
}

function PassesResult({ data }: { data: PassesResponse }) {
  if (!data.target_visible) {
    return (
      <EmptyState
        title="Target below the observability horizon"
        message={`This target only reaches ${data.target_max_alt_deg}° at ${data.site.name} during the window — it is not observably up. Try a nighttime start or a hemisphere-matched site.`}
      />
    );
  }
  return (
    <>
      <div className="ts-summary">
        <div className="ts-summary__stat">
          <span className="ts-summary__v is-signal num">{fmtInt(data.n_found)}</span>
          <span className="ts-summary__k">satellites within {data.sep_deg}°</span>
        </div>
        <div className="ts-summary__stat">
          <span className="ts-summary__v num">{data.target_max_alt_deg}°</span>
          <span className="ts-summary__k">target culmination</span>
        </div>
        <div className="ts-summary__stat">
          <span className="ts-summary__v num">{fmtInt(data.n_considered)}</span>
          <span className="ts-summary__k">LEO objects propagated</span>
        </div>
        <div className="ts-summary__stat">
          <span className="ts-summary__v num">{data.window.window_min}m</span>
          <span className="ts-summary__k">window · {data.window.step_sec}s step</span>
        </div>
        <div className="ts-summary__stat">
          <span className="ts-summary__v num">{Math.round(data.elapsed_ms)}ms</span>
          <span className="ts-summary__k">compute</span>
        </div>
      </div>

      {data.operator_tally.length ? (
        <div className="ts-tally">
          {data.operator_tally.slice(0, 8).map((o) => (
            <span key={o.operator} className="ts-tally__chip">
              {o.operator} <span className="num">{o.count}</span>
            </span>
          ))}
        </div>
      ) : null}

      {data.n_found === 0 ? (
        <EmptyState
          title="No satellites crossed the line of sight"
          message="Nothing within the threshold while the target was up. Widen the threshold or window, or try a different time."
        />
      ) : (
        <>
          <DataTable
            columns={PASS_COLUMNS}
            rows={data.passes}
            rowKey={(r) => r.norad}
            zebra
          />
          {data.truncated ? (
            <p className="hint" style={{ marginTop: 8 }}>
              Showing the {data.passes.length} closest of {fmtInt(data.n_found)} total passes.
            </p>
          ) : null}
        </>
      )}
    </>
  );
}
