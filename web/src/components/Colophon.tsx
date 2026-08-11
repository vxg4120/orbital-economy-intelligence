import { useEffect, useState } from "react";
import { getBusMethodology, getStats } from "../api/client";

/** The plate's edge-notes: every view closes under a double rule with the record's
 *  provenance in one engraved line — methodology version, freshest ingest, sources,
 *  correction channel. Replaces the idea of a footer with the idea of a colophon. */
export function Colophon() {
  const [line, setLine] = useState<{ version: string; refreshed: string } | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([getBusMethodology(), getStats()])
      .then(([m, s]) => {
        if (!alive) return;
        const stamps = s.ingest_runs
          .map((r) => r.finished_at)
          .filter(Boolean)
          .sort();
        const newest = stamps[stamps.length - 1];
        setLine({
          version: m.version,
          refreshed: newest ? String(newest).slice(0, 10) : "—",
        });
      })
      .catch(() => {
        /* the colophon is annotation, never an error surface */
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="colophon">
      <span>Orbital Economy Intelligence</span>
      <span>
        methodology <span className="num">v{line?.version ?? "—"}</span> ·{" "}
        <a href="/api/buses/methodology" target="_blank" rel="noreferrer">
          normative
        </a>
      </span>
      <span>
        refreshed <span className="num">{line?.refreshed ?? "—"}</span>
      </span>
      <span>sources: space-track · gcat · celestrak · fcc · satnogs</span>
      <span>
        corrections:{" "}
        <a
          href="https://github.com/vxg4120/orbital-economy-intelligence/issues"
          target="_blank"
          rel="noreferrer"
        >
          open an issue
        </a>
      </span>
    </div>
  );
}
