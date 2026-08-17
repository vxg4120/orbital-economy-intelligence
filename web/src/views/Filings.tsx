import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getFilingDocket, getFilingDocuments, getPendingFilings } from "../api/client";
import type { DocketResponse, PendingFiling } from "../api/types";
import { useApi } from "../hooks/useApi";
import { fmtInt } from "../lib/format";
import { Panel } from "../components/Panel";
import { Async, EmptyState } from "../components/States";

/** The pre-launch pipeline: FCC space-station applications filed and not yet decided.
 *  An authorization precedes launch by months to years, so this queue is the terminal's
 *  furthest-forward view; rows link to harvested filing documents (the narratives and
 *  technical annexes on the FCC's own gateway) and to the builder cohort where the
 *  applicant's corporate group is one. */
export function Filings() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const slug = params.get("applicant_slug") ?? "";
  const [draft, setDraft] = useState(q);
  const filings = useApi(() => getPendingFilings(q, slug, 100), [q, slug]);

  return (
    <div className="stack">
      <Panel
        title="Pending FCC applications"
        meta="the pre-launch pipeline · IBFS bulk data + harvested ICFS documents"
      >
        <p className="hint">
          Space-station applications filed with the FCC and not yet granted, denied, dismissed
          or surrendered. Filed-to-launch lead runs months to years (Starlink Gen1 filed 14
          months ahead; Kuiper 39), so nothing here exists in any tracking catalog yet.
          Builder-cohort chips link to the scoreboard page whose corporate group filed.
        </p>
        <form
          style={{ marginTop: 10, display: "flex", gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            const next = new URLSearchParams(params);
            if (draft) next.set("q", draft);
            else next.delete("q");
            setParams(next, { replace: true });
          }}
        >
          <input
            className="input"
            style={{ maxWidth: 420 }}
            placeholder="Search applicant, satellite, file number, description"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Search pending applications"
          />
          {slug ? (
            <button
              type="button"
              className="chip"
              title="Clear cohort filter"
              onClick={() => {
                const next = new URLSearchParams(params);
                next.delete("applicant_slug");
                setParams(next, { replace: true });
              }}
            >
              cohort: {slug} ✕
            </button>
          ) : null}
        </form>
      </Panel>

      <Panel title="Applications" meta="newest first" flush>
        <Async state={filings} loadingLabel="Loading pending applications">
          {(f) =>
            f.rows.length === 0 ? (
              <EmptyState title="No pending applications match" />
            ) : (
              <>
                <p className="hint" style={{ padding: "8px 14px 0" }}>
                  {fmtInt(f.total)} pending · showing {fmtInt(f.rows.length)}
                </p>
                <ul className="results">
                  {f.rows.map((r) => (
                    <FilingRow key={r.filing_key} filing={r} />
                  ))}
                </ul>
              </>
            )
          }
        </Async>
      </Panel>
    </div>
  );
}

function FilingRow({ filing: r }: { filing: PendingFiling }) {
  const [open, setOpen] = useState(false);
  return (
    <li style={{ borderBottom: "1px solid var(--rule)" }}>
      <button
        type="button"
        className="result-row"
        style={{ width: "100%", textAlign: "left", cursor: "pointer" }}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="result-row__name">
          <span className="mono-hi">{r.file_number}</span>
          {r.satellite_name ? <> · {r.satellite_name}</> : null}
          {r.note_summary ? (
            <span className="badge" style={{ marginLeft: 8 }} title="Analyst note inside">
              note
            </span>
          ) : null}
        </span>
        <span className="result-row__meta">
          {r.applicant_name}
          {r.applicant_slug ? (
            <Link
              className="chip"
              style={{ marginLeft: 8 }}
              to={`/buses/${r.applicant_slug}`}
              onClick={(e) => e.stopPropagation()}
            >
              {r.applicant_slug}
            </Link>
          ) : null}
          <span className="num" style={{ marginLeft: 10 }}>
            {r.date_filed ?? "undated"}
          </span>
          {r.documents_n > 0 ? (
            <span className="num" style={{ marginLeft: 10 }} title="harvested documents">
              {r.documents_n} docs
            </span>
          ) : null}
          <SpecChips filing={r} />
          {r.docket_filings_total && r.docket_filings_total > 1 ? (
            <span
              className="num"
              style={{ marginLeft: 10 }}
              title={
                `This callsign's docket: ${r.docket_filings_pending} pending of ` +
                `${r.docket_filings_total} filings, granted and pending together` +
                (r.docket_pending_amendments
                  ? `, ${r.docket_pending_amendments} pending amendment(s)`
                  : "") +
                ". Concurrent pending filings on one authorization are normal; no supersession " +
                "is implied. Expand the row for the timeline."
              }
            >
              docket {r.docket_filings_pending}/{r.docket_filings_total}
            </span>
          ) : null}
        </span>
      </button>
      {open ? <FilingDetail filing={r} /> : null}
    </li>
  );
}

/** Constellation shape read out of the filing's own Schedule S, or nothing when it has none.
 *
 * Every datum stays mono, per the Ledger rule. These are machine-derived, so the title attributes
 * say where each number came from rather than presenting it as catalog truth. */
function SpecChips({ filing: r }: { filing: PendingFiling }) {
  if (!r.spec_planes_n) return null;
  const alt =
    r.spec_alt_min_km != null && r.spec_alt_max_km != null
      ? r.spec_alt_min_km === r.spec_alt_max_km
        ? `${r.spec_alt_min_km} km`
        : `${r.spec_alt_min_km}-${r.spec_alt_max_km} km`
      : null;
  const inc =
    r.spec_incl_min_deg != null && r.spec_incl_max_deg != null
      ? r.spec_incl_min_deg === r.spec_incl_max_deg
        ? `${r.spec_incl_min_deg}°`
        : `${r.spec_incl_min_deg}-${r.spec_incl_max_deg}°`
      : null;
  return (
    <>
      {r.spec_total_satellites ? (
        <span className="num" style={{ marginLeft: 10 }} title="Total satellites in the active constellation, per Schedule S">
          {r.spec_total_satellites} {r.spec_total_satellites === 1 ? "sat" : "sats"}
        </span>
      ) : null}
      <span className="num" style={{ marginLeft: 10 }} title="Orbital planes listed in Schedule S">
        {r.spec_planes_n} {r.spec_planes_n === 1 ? "plane" : "planes"}
      </span>
      {alt ? (
        <span className="num" style={{ marginLeft: 10 }} title="Mean altitude across planes, from filed apogee and perigee">
          {alt}
        </span>
      ) : null}
      {inc ? (
        <span className="num" style={{ marginLeft: 10 }} title="Inclination range across planes, as filed">
          {inc}
        </span>
      ) : null}
      {r.spec_implausible_n ? (
        <span
          className="badge"
          style={{ marginLeft: 8 }}
          title={
            `${r.spec_implausible_n} plane(s) filed an apogee outside 150-50,000 km and are ` +
            "excluded from the altitude range. Not a parse error: Schedule S has no field for a " +
            "translunar trajectory, so lunar applicants enter sentinel values."
          }
        >
          lunar
        </span>
      ) : null}
    </>
  );
}

/** The filing's docket: every filing sharing its callsign, granted and pending, dated.
 *
 *  Rendered as a timeline and never as a chain. Concurrent pending filings on one authorization
 *  are the normal case, so no row is presented as superseding another, and a spec badge on a row
 *  speaks only for that filing's own validated extraction. */
function DocketTimeline({ callsign, current }: { callsign: string; current: string }) {
  const docket = useApi<DocketResponse>(() => getFilingDocket(callsign), [callsign]);
  return (
    <Async state={docket} loadingLabel="Loading docket">
      {(d) =>
        !d.summary || d.timeline.length < 2 ? null : (
          <div style={{ margin: "8px 0" }}>
            <span className="label">
              Docket {d.callsign} &middot; {d.summary.filings_pending} pending of{" "}
              {d.summary.filings_total} filings since {d.summary.first_filed ?? "?"}
            </span>
            <ul className="hint" style={{ marginTop: 4, paddingLeft: 0, listStyle: "none" }}>
              {d.timeline.map((f) => (
                <li
                  key={f.file_number}
                  style={{
                    padding: "2px 0",
                    opacity: f.is_pending ? 1 : 0.65,
                    fontWeight: f.file_number === current ? 600 : 400,
                  }}
                >
                  <span className="num">{f.date_filed ?? "undated"}</span>
                  {" · "}
                  <span className="mono-hi">{f.file_number}</span>
                  {" · "}
                  {f.app_type_code ?? "?"}
                  {" · "}
                  {f.is_pending ? "pending" : f.date_grant ? `granted ${f.date_grant}` : "decided"}
                  {f.spec_available ? (
                    <span className="badge" style={{ marginLeft: 6 }} title="Validated Schedule S extraction for this filing">
                      spec
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
            <p className="hint" style={{ marginTop: 4 }}>
              Timeline, not a chain: concurrent pending filings on one authorization are normal,
              and no supersession is implied.
            </p>
          </div>
        )
      }
    </Async>
  );
}

function FilingDetail({ filing: r }: { filing: PendingFiling }) {
  const docs = useApi(() => getFilingDocuments(r.file_number), [r.file_number]);
  return (
    <div className="panel__body" style={{ background: "var(--surface-2)" }}>
      {r.description ? <p className="hint">{r.description}</p> : null}
      {r.callsign && r.docket_filings_total && r.docket_filings_total > 1 ? (
        <DocketTimeline callsign={r.callsign} current={r.file_number} />
      ) : null}
      {r.note_summary ? (
        <div style={{ margin: "8px 0" }}>
          <span className="label">Analyst note</span>
          <p style={{ marginTop: 4 }}>{r.note_summary}</p>
          {r.note_key_points?.length ? (
            <ul className="hint" style={{ marginTop: 4, paddingLeft: 18 }}>
              {r.note_key_points.map((k) => (
                <li key={k}>{k}</li>
              ))}
            </ul>
          ) : null}
          {r.note_source_doc ? (
            <p className="hint" style={{ marginTop: 4 }}>
              source: {r.note_source_doc}
              {r.note_source_pages ? `, ${r.note_source_pages}` : ""}
            </p>
          ) : null}
        </div>
      ) : null}
      <Async state={docs} loadingLabel="Loading documents">
        {(d) =>
          d.documents.length === 0 ? (
            <p className="hint">
              No harvested documents for this filing yet (harvest covers builder-cohort
              filings; the FCC portal has the full record).
            </p>
          ) : (
            <div>
              <span className="label">Filing documents · direct FCC downloads</span>
              <ul className="results" style={{ marginTop: 4 }}>
                {d.documents.map((doc) => (
                  <li key={doc.download_url}>
                    <a
                      className="result-row"
                      href={doc.download_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <span className="result-row__name mono-hi">{doc.doc_name}</span>
                      <span className="result-row__meta num">{doc.doc_date ?? ""}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )
        }
      </Async>
    </div>
  );
}
