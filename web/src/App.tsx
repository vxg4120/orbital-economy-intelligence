import { lazy, Suspense, useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { getStats, MOCK } from "./api/client";
import { useApi } from "./hooks/useApi";
import { compact, fmtPct } from "./lib/format";
import { SourceBadge } from "./components/SourceBadge";
import { Loading } from "./components/States";
import { maybeAutoStartTour, startTour } from "./lib/tour";

// Code-split each view so the heavy chart dependency (recharts, Operators only)
// never inflates the initial bundle.
const Overview = lazy(() => import("./views/Overview").then((m) => ({ default: m.Overview })));
const Resolver = lazy(() => import("./views/Resolver").then((m) => ({ default: m.Resolver })));
const Conflicts = lazy(() => import("./views/Conflicts").then((m) => ({ default: m.Conflicts })));
const Operators = lazy(() => import("./views/Operators").then((m) => ({ default: m.Operators })));
const Review = lazy(() => import("./views/Review").then((m) => ({ default: m.Review })));
const ReviewCase = lazy(() => import("./views/ReviewCase").then((m) => ({ default: m.ReviewCase })));

const NAV = [
  { to: "/", idx: "00", name: "Overview", end: true },
  { to: "/resolver", idx: "01", name: "Resolver", end: false },
  { to: "/conflicts", idx: "02", name: "Conflicts", end: false },
  { to: "/operators", idx: "03", name: "Operators", end: false },
  { to: "/review", idx: "04", name: "Review", end: false },
];

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const p = (x: number) => String(x).padStart(2, "0");
  const stamp = `${now.getUTCFullYear()}-${p(now.getUTCMonth() + 1)}-${p(now.getUTCDate())} ${p(
    now.getUTCHours(),
  )}:${p(now.getUTCMinutes())}:${p(now.getUTCSeconds())}`;
  return (
    <div className="topbar__clock">
      <span className="tele__k">{MOCK ? "mock · utc" : "live · utc"}</span>
      <span className="tele__v num">{stamp}</span>
    </div>
  );
}

function Telemetry() {
  const stats = useApi(() => getStats(), []);
  const s = stats.data;
  const conflictTotal = s ? s.conflicts.status + s.conflicts.decay + s.conflicts.stale_owners : null;
  const chip = (k: string, v: string, cls = "") => (
    <div className="tele">
      <span className="tele__k">{k}</span>
      <span className={`tele__v ${cls}`}>{v}</span>
    </div>
  );
  return (
    <div className="topbar__tele">
      {chip("objects", s ? compact(s.satellites) : "····")}
      {chip("operators", s ? compact(s.operators) : "····")}
      {chip("element sets", s ? compact(s.gp_elements) : "····")}
      {chip("op coverage", s ? fmtPct(s.coverage.operator_pct) : "····", "is-signal")}
      {chip("conflicts", conflictTotal !== null ? compact(conflictTotal) : "····", "is-conflict")}
    </div>
  );
}

export default function App() {
  useEffect(() => {
    maybeAutoStartTour();
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__brand">
          <span className="topbar__mark">
            <span className="dot" aria-hidden="true" />
            Orbital Terminal
          </span>
          <span className="topbar__sub">Identity Graph · Read-only</span>
        </div>
        <Telemetry />
        <Clock />
        <button
          type="button"
          className="topbar__help"
          onClick={() => startTour()}
          title="Replay the intro tour"
          aria-label="Replay the intro tour"
        >
          ?
        </button>
      </header>

      <nav className="rail" aria-label="Views">
        <div className="rail__nav" data-tour="nav">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              data-tour={n.to === "/resolver" ? "resolver" : undefined}
              className={({ isActive }) => `navlink${isActive ? " is-active" : ""}`}
            >
              <span className="navlink__idx num">{n.idx}</span>
              <span className="navlink__name">{n.name}</span>
            </NavLink>
          ))}
        </div>
        <div className="rail__foot">
          <span className="label">Provenance</span>
          <div className="srckey">
            <SourceBadge source="satcat" />
            <SourceBadge source="gcat" />
            <SourceBadge source="ucs" />
            <SourceBadge source="resolve" />
          </div>
          <span className="hint">
            {MOCK ? "Fixtures — API offline" : "Proxy → :8600"}
          </span>
        </div>
      </nav>

      <main className="main">
        <Suspense fallback={<Loading label="Loading view" />}>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/resolver" element={<Resolver />} />
            <Route path="/resolver/:satelliteId" element={<Resolver />} />
            <Route path="/conflicts" element={<Conflicts />} />
            <Route path="/operators" element={<Operators />} />
            <Route path="/operators/:operatorId" element={<Operators />} />
            <Route path="/review" element={<Review />} />
            <Route path="/review/:caseId" element={<ReviewCase />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}
