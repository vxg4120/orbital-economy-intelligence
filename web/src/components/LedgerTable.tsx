import type { IngestRun } from "../api/types";
import { fmtDateTime, fmtInt } from "../lib/format";
import { runStatusClass } from "../lib/status";
import { SourceBadge } from "./SourceBadge";

/** Ingestion ledger — politeness made visible: last run per source/endpoint,
    with ok/skipped_fresh/error state as a colored dot + label. */
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
          {runs.map((r, i) => (
            <tr key={`${r.source}-${r.endpoint}-${r.status}-${i}`}>
              <td>
                <SourceBadge source={r.source} />
              </td>
              <td className="mono-hi">{r.endpoint}</td>
              <td>
                <span className="run-state">
                  <span className={`run-dot ${runStatusClass(r.status)}`} aria-hidden="true" />
                  {r.status}
                </span>
              </td>
              <td className="is-num mono-hi">{fmtInt(r.rows_ingested)}</td>
              <td className="num">{fmtDateTime(r.finished_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
