import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { getReachability, getReachabilityPasses } from "../api/client";
import type {
  FccLicense,
  ReachabilityResponse,
  ReachabilityRf,
  SatnogsTransmitter,
  VisibleSatellite,
} from "../api/types";
import { useApi } from "../hooks/useApi";
import { DASH, fmtDateTime, fmtInt, fmtNum } from "../lib/format";
import { Panel } from "../components/Panel";
import { Async, EmptyState, ErrorState, Loading } from "../components/States";

const MIN_ELEV_OPTIONS = [0, 10, 20, 30];
const SWEEP_SECONDS = 60;
const SWEEP_LIMIT = 60;
const PASS_WINDOW_HOURS = 24;

/* 16-wind compass point for an azimuth in degrees. */
const WINDS = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
];
function compass(azimuthDeg: number): string {
  const norm = ((azimuthDeg % 360) + 360) % 360;
  return WINDS[Math.round(norm / 22.5) % 16];
}

/** Hz from the API -> "437.800 MHz". */
function fmtMHz(hz: number | null | undefined): string {
  if (hz === null || hz === undefined) return DASH;
  return `${(hz / 1e6).toFixed(3)} MHz`;
}

/** ISO instant -> local wall-clock time; adds month + day when it is not today. */
function fmtLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (x: number) => String(x).padStart(2, "0");
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  const month = d.toLocaleString("en-US", { month: "short" });
  return `${month} ${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

function parseCoord(text: string, bound: number): number | null {
  const t = text.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && Math.abs(n) <= bound ? n : null;
}

/** The downlink a ground listener is most likely to actually hear: confirmed
    entries beat unconfirmed ones, then the lowest frequency wins (VHF/UHF gear
    is common; microwave is not). */
function primaryDownlink(sat: VisibleSatellite): SatnogsTransmitter | null {
  const withDown = sat.satnogs.filter((t) => t.downlink_low !== null);
  if (withDown.length === 0) return null;
  const confirmed = withDown.filter((t) => !t.unconfirmed);
  const pool = confirmed.length > 0 ? confirmed : withDown;
  return pool.reduce((best, t) =>
    (t.downlink_low ?? Infinity) < (best.downlink_low ?? Infinity) ? t : best,
  );
}

interface Spot {
  lat: number;
  lon: number;
}

interface SweepState {
  data: ReachabilityResponse | null;
  loading: boolean;
  error: string | null;
}

export function Reachability() {
  const [latText, setLatText] = useState("");
  const [lonText, setLonText] = useState("");
  const [minElev, setMinElev] = useState(10);
  const [rf, setRf] = useState<ReachabilityRf>("only");
  const [geoNote, setGeoNote] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  const lat = parseCoord(latText, 90);
  const lon = parseCoord(lonText, 180);

  // Debounce the typed coordinates so a half-typed longitude never fires a sweep.
  const [spot, setSpot] = useState<Spot | null>(null);
  useEffect(() => {
    if (lat === null || lon === null) {
      setSpot(null);
      return;
    }
    const t = setTimeout(() => setSpot({ lat, lon }), 450);
    return () => clearTimeout(t);
  }, [lat, lon]);

  // The sweep keeps the previous rows on screen while a refresh is in flight;
  // useApi clears data on every run, which would blank the table each minute.
  const [sweep, setSweep] = useState<SweepState>({ data: null, loading: false, error: null });
  const [nonce, setNonce] = useState(0);
  const resweep = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!spot) {
      setSweep({ data: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setSweep((s) => ({ ...s, loading: true, error: null }));
    getReachability(spot.lat, spot.lon, { minElev, rf, limit: SWEEP_LIMIT })
      .then((r) => {
        if (!cancelled) setSweep({ data: r, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSweep((s) => ({
            data: s.data,
            loading: false,
            error: err instanceof Error ? err.message : "Request failed",
          }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [spot, minElev, rf, nonce]);

  // 60 s auto-refresh with a visible countdown. A hidden tab skips the fetch and
  // keeps counting, so a backgrounded terminal does not keep hitting the API.
  const [countdown, setCountdown] = useState(SWEEP_SECONDS);
  const tickRef = useRef(SWEEP_SECONDS);
  useEffect(() => {
    if (!spot) return;
    tickRef.current = SWEEP_SECONDS;
    setCountdown(SWEEP_SECONDS);
    const id = setInterval(() => {
      tickRef.current -= 1;
      if (tickRef.current <= 0) {
        tickRef.current = SWEEP_SECONDS;
        if (!document.hidden) resweep();
      }
      setCountdown(tickRef.current);
    }, 1000);
    return () => clearInterval(id);
  }, [spot, minElev, rf, resweep]);

  const sweepNow = () => {
    tickRef.current = SWEEP_SECONDS;
    setCountdown(SWEEP_SECONDS);
    resweep();
  };

  const useMyLocation = () => {
    if (!("geolocation" in navigator)) {
      setGeoNote("This browser does not offer geolocation. Type coordinates in by hand.");
      return;
    }
    setLocating(true);
    setGeoNote(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        setLatText(pos.coords.latitude.toFixed(4));
        setLonText(pos.coords.longitude.toFixed(4));
      },
      (err) => {
        setLocating(false);
        setGeoNote(
          err.code === err.PERMISSION_DENIED
            ? "Location permission denied. No problem: type coordinates in by hand; they are used only for the sweep query and never stored."
            : "Could not get a position fix. Type coordinates in by hand.",
        );
      },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
    );
  };

  const badCoord = (latText.trim() !== "" && lat === null) || (lonText.trim() !== "" && lon === null);
  const rows = sweep.data ? [...sweep.data.visible].sort((a, b) => b.elevation_deg - a.elevation_deg) : [];

  const sweepMeta = sweep.data
    ? `${sweep.loading ? "sweeping · " : ""}computed ${fmtDateTime(sweep.data.computed_at)} · ${fmtInt(
        sweep.data.candidates_screened,
      )} screened · next in ${countdown}s`
    : spot
      ? "first sweep"
      : "waiting for a location";

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Reachability</h1>
          <p className="vhead__desc">
            Stand somewhere and ask what the sky offers. Give the terminal a spot on Earth and it
            computes every satellite above your horizon right now, the radio layers each one
            carries, and when the next passes come over. Geometry from the latest GP elements;
            transmitters from SatNOGS; licensing context from FCC filings.
          </p>
        </div>
      </header>

      <Panel
        title="Observer"
        meta={
          spot
            ? `${fmtNum(spot.lat, 4)}, ${fmtNum(spot.lon, 4)} · min elev ${minElev}°`
            : "no location set"
        }
      >
        <div className="reach-controls">
          <button className="btn btn--go" onClick={useMyLocation} disabled={locating}>
            {locating ? "Locating" : "Use my location"}
          </button>
          <label className="reach-coord">
            <span className="label">Lat</span>
            <input
              className="vinput"
              type="number"
              step="any"
              min={-90}
              max={90}
              value={latText}
              onChange={(e) => setLatText(e.target.value)}
              placeholder="34.0522"
              aria-label="Latitude in decimal degrees"
            />
          </label>
          <label className="reach-coord">
            <span className="label">Lon</span>
            <input
              className="vinput"
              type="number"
              step="any"
              min={-180}
              max={180}
              value={lonText}
              onChange={(e) => setLonText(e.target.value)}
              placeholder="-118.2437"
              aria-label="Longitude in decimal degrees"
            />
          </label>
          <div className="tabs tabs--sub" aria-label="Minimum elevation above the horizon">
            {MIN_ELEV_OPTIONS.map((e) => (
              <button
                key={e}
                className={`tab${minElev === e ? " is-active" : ""}`}
                title={`Only satellites at least ${e}° above the horizon`}
                onClick={() => setMinElev(e)}
              >
                {e}°
              </button>
            ))}
          </div>
          <div className="tabs tabs--sub" aria-label="RF filter">
            <button
              className={`tab${rf === "only" ? " is-active" : ""}`}
              title="Only satellites with a catalogued transmitter or FCC license"
              onClick={() => setRf("only")}
            >
              RF only
            </button>
            <button
              className={`tab${rf === "all" ? " is-active" : ""}`}
              title="Everything overhead, radio-silent objects included"
              onClick={() => setRf("all")}
            >
              All overhead
            </button>
          </div>
          <button className="btn" onClick={sweepNow} disabled={!spot || sweep.loading}>
            Sweep now
          </button>
        </div>
        {geoNote ? (
          <p className="hint" style={{ marginTop: 10 }}>
            {geoNote}
          </p>
        ) : null}
        {badCoord ? (
          <p className="hint" style={{ marginTop: 10 }}>
            Coordinates are decimal degrees: latitude -90 to 90, longitude -180 to 180.
          </p>
        ) : null}
      </Panel>

      <Panel title="Visible now" meta={sweepMeta} flush>
        {!spot ? (
          <EmptyState
            title="No observer set"
            message="Use your location, or type a latitude and longitude, and the terminal will sweep the sky above it."
          />
        ) : sweep.error && sweep.data === null ? (
          <ErrorState message={sweep.error} onRetry={sweepNow} />
        ) : sweep.loading && sweep.data === null ? (
          <Loading label="Sweeping the sky" />
        ) : sweep.data && rows.length === 0 ? (
          <EmptyState
            title="Quiet sky"
            message={`Nothing${rf === "only" ? " with catalogued RF" : ""} is above ${minElev}° here right now. Low orbits move fast; check back in a minute or two, or lower the elevation floor.`}
          />
        ) : sweep.data ? (
          <>
            {sweep.error ? (
              <p className="conflict-headline">
                Last sweep failed ({sweep.error}); showing the previous result.
              </p>
            ) : null}
            <VisibleTable
              rows={rows}
              spot={spot}
              minElev={minElev}
              selected={selected}
              onSelect={(id) => setSelected((cur) => (cur === id ? null : id))}
            />
          </>
        ) : null}
      </Panel>

      <footer className="reach-attrib hint">
        {sweep.data?.attribution ??
          "Transmitter data from SatNOGS DB (https://db.satnogs.org), CC BY-SA 4.0."}{" "}
        Orbit geometry is computed from public GP element sets. FCC entries are license context
        matched at the operator or constellation level, not proof of an active emission at your
        location.
      </footer>
    </div>
  );
}

function VisibleTable({
  rows,
  spot,
  minElev,
  selected,
  onSelect,
}: {
  rows: VisibleSatellite[];
  spot: Spot;
  minElev: number;
  selected: number | null;
  onSelect: (noradId: number) => void;
}) {
  return (
    <div className="table-wrap">
      <table className="dtable">
        <thead>
          <tr>
            <th>Satellite</th>
            <th className="is-num">Elev</th>
            <th className="is-num">Azimuth</th>
            <th className="is-num">Range km</th>
            <th>RF layers</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((v) => {
            const open = selected === v.norad_id;
            return (
              <Fragment key={v.norad_id}>
                <tr
                  className={`is-link reach-row${open ? " is-open" : ""}`}
                  tabIndex={0}
                  aria-expanded={open}
                  onClick={() => onSelect(v.norad_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(v.norad_id);
                    }
                  }}
                >
                  <td>
                    <span className="mono-hi">{v.name}</span>
                    <span className="muted"> · {v.norad_id}</span>
                  </td>
                  <td className="is-num">{fmtNum(v.elevation_deg, 1)}°</td>
                  <td className="is-num">
                    {fmtNum(v.azimuth_deg, 1)}° <span className="muted">{compass(v.azimuth_deg)}</span>
                  </td>
                  <td className="is-num">{fmtNum(v.range_km, 0)}</td>
                  <td>
                    <RfSummary sat={v} />
                  </td>
                </tr>
                {open ? (
                  <tr className="reach-expand">
                    <td colSpan={5}>
                      <SatDetail sat={v} spot={spot} minElev={minElev} />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Compact RF cell: the primary SatNOGS downlink in MHz + mode, and the first
    FCC call sign as a chip. Either layer can be absent. */
function RfSummary({ sat }: { sat: VisibleSatellite }) {
  const main = primaryDownlink(sat);
  const extraTx = sat.satnogs.length - (main ? 1 : 0);
  const callSign = sat.fcc.length > 0 ? sat.fcc[0].call_sign.split(" / ")[0] : null;
  if (!main && callSign === null) return <span className="dash">{DASH}</span>;
  return (
    <span className="inline">
      {main ? (
        <span>
          <span className="num mono-hi">{fmtMHz(main.downlink_low)}</span>
          {main.mode ? <span className="muted"> {main.mode}</span> : null}
          {extraTx > 0 ? <span className="muted"> +{extraTx}</span> : null}
        </span>
      ) : null}
      {callSign !== null ? (
        <span className="chip" title={sat.fcc.map((f) => f.call_sign).join(", ")}>
          {callSign}
          {sat.fcc.length > 1 ? ` +${sat.fcc.length - 1}` : ""}
        </span>
      ) : null}
    </span>
  );
}

/** Inline detail for one selected satellite: the full transmitter and license
    lists from the sweep row (already loaded), plus the pass schedule fetched
    from /reachability/passes. */
function SatDetail({ sat, spot, minElev }: { sat: VisibleSatellite; spot: Spot; minElev: number }) {
  const passes = useApi(
    () => getReachabilityPasses(spot.lat, spot.lon, sat.norad_id, { hours: PASS_WINDOW_HOURS, minElev }),
    [sat.norad_id, spot.lat, spot.lon, minElev],
  );
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;

  return (
    <div className="reach-detail">
      <div className="reach-detail__cols">
        <section>
          <div className="reach-detail__head">
            <span className="label">Transmitters · SatNOGS</span>
            <span className="hint">{fmtInt(sat.satnogs.length)} catalogued</span>
          </div>
          {sat.satnogs.length === 0 ? (
            <p className="hint">No community-catalogued transmitters for this object.</p>
          ) : (
            <div className="tx-list">
              {sat.satnogs.map((t, i) => (
                <Transmitter key={i} t={t} />
              ))}
            </div>
          )}

          <div className="reach-detail__head" style={{ marginTop: 14 }}>
            <span className="label">FCC licensing</span>
            <span className="hint">{fmtInt(sat.fcc.length)} matches</span>
          </div>
          {sat.fcc.length === 0 ? (
            <p className="hint">No FCC license match on record.</p>
          ) : (
            <div className="tx-list">
              {sat.fcc.map((f, i) => (
                <FccEntry key={i} f={f} />
              ))}
            </div>
          )}
        </section>

        <section>
          <div className="reach-detail__head">
            <span className="label">Pass schedule</span>
            <span className="hint">
              next {PASS_WINDOW_HOURS} h · local time ({tz})
            </span>
          </div>
          <Async state={passes} loadingLabel="Computing passes">
            {(p) =>
              p.passes.length === 0 ? (
                <p className="hint">
                  No passes above {minElev}° from this spot in the next {PASS_WINDOW_HOURS} hours.
                </p>
              ) : (
                <div className="table-wrap">
                  <table className="dtable">
                    <thead>
                      <tr>
                        <th>Rise</th>
                        <th>Peak</th>
                        <th className="is-num">Max elev</th>
                        <th>Set</th>
                        <th className="is-num">Length</th>
                      </tr>
                    </thead>
                    <tbody>
                      {p.passes.map((x, i) => (
                        <tr key={i}>
                          <td>
                            <span className="num">{fmtLocal(x.rise)}</span>{" "}
                            <span className="muted">
                              {compass(x.rise_azimuth_deg)} {fmtNum(x.rise_azimuth_deg, 0)}°
                            </span>
                          </td>
                          <td>
                            <span className="num">{fmtLocal(x.peak)}</span>{" "}
                            <span className="muted">
                              {compass(x.peak_azimuth_deg)} {fmtNum(x.peak_azimuth_deg, 0)}°
                            </span>
                          </td>
                          <td className="is-num">{fmtNum(x.peak_elevation_deg, 1)}°</td>
                          <td>
                            <span className="num">{fmtLocal(x.set)}</span>{" "}
                            <span className="muted">
                              {compass(x.set_azimuth_deg)} {fmtNum(x.set_azimuth_deg, 0)}°
                            </span>
                          </td>
                          <td className="is-num">{fmtDuration(x.duration_s)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Async>
          <p className="hint" style={{ marginTop: 10 }}>
            Predictions use the element set from {fmtDateTime(sat.element_epoch)}; accuracy drifts
            as elements age.
          </p>
        </section>
      </div>
    </div>
  );
}

function Transmitter({ t }: { t: SatnogsTransmitter }) {
  return (
    <div className="tx">
      <div className="tx__head">
        <span className="tx__desc">{t.description}</span>
        {t.unconfirmed ? (
          <span className="badge badge--conflict" title="SatNOGS marks this entry unconfirmed">
            <span className="badge__glyph" aria-hidden="true" />
            unconfirmed
          </span>
        ) : null}
      </div>
      <div className="tx__freq num">
        {t.downlink_low !== null ? (
          <>
            down {fmtMHz(t.downlink_low)}
            {t.downlink_high !== null ? ` to ${fmtMHz(t.downlink_high)}` : ""}
          </>
        ) : (
          <span className="muted">no downlink listed</span>
        )}
        {t.uplink_low !== null ? <span className="muted"> · up {fmtMHz(t.uplink_low)}</span> : null}
      </div>
      <div className="hint">
        {t.type}
        {t.mode ? ` · ${t.mode}` : ""}
        {t.baud ? ` · ${fmtInt(Math.round(t.baud))} Bd` : ""}
        {t.service ? ` · ${t.service}` : ""}
        {" · "}
        <CitationLink citation={t.citation} />
      </div>
    </div>
  );
}

/** SatNOGS citations are free text; link only the ones that are URLs. */
function CitationLink({ citation }: { citation: string }) {
  if (/^https?:\/\//i.test(citation)) {
    let label = "source";
    try {
      label = new URL(citation).hostname.replace(/^www\./, "");
    } catch {
      /* malformed URL: keep the generic label */
    }
    return (
      <a href={citation} target="_blank" rel="noreferrer">
        {label}
      </a>
    );
  }
  return <span>{citation}</span>;
}

function FccEntry({ f }: { f: FccLicense }) {
  const meta = [
    f.service,
    f.grant_type,
    f.match_tier ? `matched at ${f.match_tier} level` : null,
  ].filter(Boolean);
  return (
    <div className="tx">
      <div className="tx__head">
        <span className="chip">{f.call_sign}</span>
        {f.licensee ? <span className="tx__desc">{f.licensee}</span> : null}
      </div>
      {f.frequency_range ? <p className="fcc-bands num">{f.frequency_range}</p> : null}
      {meta.length > 0 ? <div className="hint">{meta.join(" · ")}</div> : null}
    </div>
  );
}
