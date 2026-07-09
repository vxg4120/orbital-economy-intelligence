import type { IngestRun } from "../api/types";
import { fmtDateTime, fmtInt } from "../lib/format";
import { runStatusMeta } from "../lib/status";
import { SourceBadge } from "./SourceBadge";

/** Ingestion ledger — politeness made visible: last run per source/endpoint,
    with ok/skipped_fresh/error/running state as a colored dot + label. Every cell renders
    null-safely (in-flight rows carry a null status and no finished_at / rows yet). */
export function LedgerTable({ runs }: { runs: IngestRun[] }) {
  return (
    <div className="table-wrap">
      <table className="dtable">
        <thead>
          <tr>
            <th>Source</th>
            <th>Endpoint</th>
            <th>State</th>
            <th className="is-num">Rows</th>
            <th>Finished</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r, i) => {
            const st = runStatusMeta(r.status);
            return (
              <tr key={`${r.source}-${r.endpoint}-${r.status ?? "running"}-${i}`}>
                <td>
                  <SourceBadge source={r.source} />
                </td>
                <td className="mono-hi">{r.endpoint}</td>
                <td>
                  <span className="run-state">
                    <span className={`run-dot ${st.className}`} aria-hidden="true" />
                    {st.label}
                  </span>
                </td>
                <td className="is-num mono-hi">{fmtInt(r.rows_ingested)}</td>
                <td className="num">{fmtDateTime(r.finished_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
