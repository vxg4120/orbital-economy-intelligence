import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getFilingDocket, getFilingDocuments, getFilingsMethodology, getPendingFilings } from "../api/client";
import type { DocketResponse, FilingsMethodology, PendingFiling } from "../api/types";
import { useApi } from "../hooks/useApi";
import { fmtInt } from "../lib/format";
import { Panel } from "../components/Panel";
import { Pager } from "../components/DataTable";
import { Async, EmptyState } from "../components/States";

const LIMIT = 100;

/** IBFS application types, for the file-number tooltip. A file number is SAT + type + date +
    sequence, and three types carry a literal slash: "SATT/C2025052000121" is a transfer of
    control, not a malformed STA. Codes outside this list show as themselves. */
const APP_TYPES: Record<string, string> = {
  LOA: "space station license application",
  STA: "special temporary authority",
  MOD: "modification",
  AMD: "amendment",
  LOI: "letter of intent (non-US licensed system)",
  PDR: "petition for declaratory ruling (market access)",
  APL: "amendment to a petition for declaratory ruling",
  MPL: "modification of a petition for declaratory ruling",
  "T/C": "transfer of control",
  ASG: "assignment",
  RPL: "replacement",
  MSC: "miscellaneous",
};

function fileNumberTitle(r: PendingFiling): string {
  const code = r.app_type_code ?? "";
  const label = APP_TYPES[code];
  return code
    ? `FCC IBFS file number · application type ${code}${label ? `: ${label}` : ""}`
    : "FCC IBFS file number";
}

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
  // The query lives in the URL (a search is a link). The box commits to it as you type, after
  // a short pause, like the resolver and the buses, so nobody has to know to press Enter; Enter
  // commits at once. `committed` is what the URL already holds, so a URL change from outside
  // (the back button, the clear button) never schedules a stale commit over it.
  const committed = useRef(q);
  const commit = (value: string) => {
    if (value === committed.current) return;
    committed.current = value;
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("q", value);
        else next.delete("q");
        return next;
      },
      { replace: true },
    );
  };
  useEffect(() => {
    if (draft === committed.current) return;
    const t = setTimeout(() => commit(draft), 250);
    return () => clearTimeout(t);
  }, [draft]);
  useEffect(() => {
    committed.current = q;
    setDraft(q);
  }, [q]);
  const clearSearch = () => {
    setDraft("");
    commit("");
  };
  // One page offset per filter, so a filter change never first asks for the old page.
  const [offsets, setOffsets] = useState<Record<string, number>>({});
  const filterKey = `${q}|${slug}`;
  const offset = offsets[filterKey] ?? 0;
  const setOffset = (next: number) => setOffsets((o) => ({ ...o, [filterKey]: next }));
  // keep: a page or search change dims the list in place instead of swapping it for a spinner
  const filings = useApi(() => getPendingFilings(q, slug, LIMIT, offset), [q, slug, offset], {
    keep: true,
  });
  const methodology = useApi<FilingsMethodology>(() => getFilingsMethodology(), []);

  return (
    <div className="view fadein">
      <header className="vhead">
        <div>
          <h1 className="vhead__title">Filings</h1>
          <p className="vhead__desc">
            The pre-launch pipeline: space-station applications filed with the FCC and not yet
            granted, denied, dismissed or surrendered. Filed-to-launch lead runs months to years
            (Starlink Gen1 filed 14 months ahead; Kuiper 39), so nothing here exists in any
            tracking catalog yet. Builder-cohort chips link to the scoreboard page whose
            corporate group filed.
          </p>
        </div>
      </header>

      <form
        className="searchbar"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          commit(draft);
        }}
      >
        <span className="searchbar__prompt" aria-hidden="true">
          ⌕
        </span>
        <input
          type="search"
          placeholder="Search applicant, satellite, file number or description"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          aria-label="Search pending applications"
          autoComplete="off"
          spellCheck={false}
        />
        {slug ? (
          <button
            type="button"
            className="chip chip--clear"
            title="Clear cohort filter"
            onClick={() =>
              setParams(
                (prev) => {
                  const next = new URLSearchParams(prev);
                  next.delete("applicant_slug");
                  return next;
                },
                { replace: true },
              )
            }
          >
            cohort: {slug} ✕
          </button>
        ) : null}
      </form>

      <Panel title="Applications" meta="newest first · IBFS bulk data + harvested ICFS documents" flush>
        <Async state={filings} loadingLabel="Loading pending applications" loadingRows={8}>
          {(f) => (
            <>
              <p className="hint" style={{ padding: "8px 14px 0" }}>
                {fmtInt(f.total)} pending
                {f.newest_filed ? ` · newest filing ${f.newest_filed}` : ""}
              </p>
              {f.newest_filed ? (
                <p className="hint" style={{ padding: "2px 14px 0" }}>
                  This list is a snapshot. The FCC now takes new satellite applications through
                  ICFS, which this pipeline does not read yet, so nothing filed after{" "}
                  <span className="num">{f.newest_filed}</span> appears here, and some of these
                  filings may have been decided since.
                </p>
              ) : null}
              {f.rows.length === 0 ? (
                <EmptyState
                  title={q || slug ? "No pending applications match" : "No pending applications"}
                  message={
                    q
                      ? `Nothing matches “${q}”${slug ? ` in cohort ${slug}` : ""}. Try an applicant, a satellite name or an IBFS file number, or clear the search.`
                      : slug
                        ? `Nothing is pending from cohort ${slug}. Clear the cohort filter to see every application.`
                        : "The pending queue is empty."
                  }
                  action={
                    q ? (
                      <button type="button" className="chip chip--clear" onClick={clearSearch}>
                        clear search ✕
                      </button>
                    ) : undefined
                  }
                />
              ) : (
                <>
                  <ul className="results">
                    {f.rows.map((r) => (
                      <FilingRow key={r.filing_key} filing={r} />
                    ))}
                  </ul>
                  <Pager
                    offset={offset}
                    limit={LIMIT}
                    total={f.total}
                    shown={f.rows.length}
                    onOffset={setOffset}
                  />
                </>
              )}
            </>
          )}
        </Async>
      </Panel>

      <Panel
        title="Methodology"
        meta={
          methodology.data
            ? `${methodology.data.version} · as of ${methodology.data.as_of} · full doc on GitHub`
            : undefined
        }
      >
        <Async state={methodology} loadingLabel="Loading methodology">
          {(m) => (
            <div className="stack" style={{ gap: 10 }}>
              <div>
                <span className="label">Purpose</span>
                <p className="hint" style={{ marginTop: 4 }}>
                  {m.purpose}
                </p>
              </div>
              <div>
                <span className="label">Pipeline</span>
                <ol className="hint" style={{ marginTop: 4, paddingLeft: 18 }}>
                  {m.pipeline.map((step) => (
                    <li key={step.slice(0, 40)}>{step}</li>
                  ))}
                </ol>
              </div>
              <div>
                <span className="label">Coverage, as of {m.as_of}</span>
                <ul className="hint" style={{ marginTop: 4, paddingLeft: 18 }}>
                  {Object.entries(m.coverage).map(([k, v]) => (
                    <li key={k}>
                      {coverageLabel(k)}:{" "}
                      <span className="num">{typeof v === "number" ? fmtInt(v) : String(v)}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <span className="label">Docket model</span>
                <p className="hint" style={{ marginTop: 4 }}>
                  {m.docket_model.model} {m.docket_model.why} {m.docket_model.scale}
                </p>
              </div>
              <div>
                <span className="label">Caveats</span>
                <ul className="hint" style={{ marginTop: 4, paddingLeft: 18 }}>
                  {m.caveats.map((c) => (
                    <li key={c.slice(0, 40)}>{c}</li>
                  ))}
                </ul>
              </div>
              <p className="hint">{m.no_llm}</p>
              <p className="hint">
                Full document:{" "}
                <a
                  href="https://github.com/vxg4120/orbital-economy-intelligence/blob/main/docs/FCC_FILINGS_METHODOLOGY.md"
                  target="_blank"
                  rel="noopener"
                >
                  FCC_FILINGS_METHODOLOGY.md
                </a>
              </p>
            </div>
          )}
        </Async>
      </Panel>
    </div>
  );
}

/** A methodology coverage key as a phrase: underscores to spaces, and the proper names the
    keys spell in lowercase (NGSO, Schedule S, PDF) restored. */
function coverageLabel(key: string): string {
  return key
    .replaceAll("_", " ")
    .replace(/\bngso\b/gi, "NGSO")
    .replace(/\bschedule s\b/gi, "Schedule S")
    .replace(/\bpdf(s?)\b/gi, (_, s: string) => `PDF${s}`);
}

function FilingRow({ filing: r }: { filing: PendingFiling }) {
  const [open, setOpen] = useState(false);
  return (
    <li style={{ borderBottom: "1px solid var(--rule)" }}>
      {/* The row is a div, not a button: the cohort chip inside it is a link, and interactive
          content inside a button is invalid HTML that assistive tech handles unevenly. The
          mouse still toggles anywhere on the row; the keyboard has the explicit toggle. */}
      <div
        className="result-row filing-row"
        style={{ width: "100%", textAlign: "left", cursor: "pointer" }}
        onClick={() => setOpen(!open)}
      >
        <span className="result-row__name">
          <span className="mono-hi" title={fileNumberTitle(r)}>{r.file_number}</span>
          {r.satellite_name ? <> · {r.satellite_name}</> : null}
          {r.note_summary ? (
            <span className="badge" style={{ marginLeft: 8 }} title="Analyst note inside">
              note
            </span>
          ) : null}
        </span>
        {/* a wrapping chip row (theme.css .filing-row): the gap spaces the chips */}
        <span className="result-row__meta">
          {r.applicant_name}
          {r.applicant_slug ? (
            <Link
              className="chip"
              to={`/buses/${r.applicant_slug}`}
              onClick={(e) => e.stopPropagation()}
            >
              {r.applicant_slug}
            </Link>
          ) : null}
          <span className="num">{r.date_filed ?? "undated"}</span>
          {r.documents_n > 0 ? (
            <span className="num" title="harvested documents">
              {r.documents_n} docs
            </span>
          ) : null}
          <SpecChips filing={r} />
          {r.docket_filings_total && r.docket_filings_total > 1 ? (
            <span
              className="num"
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
              docket {r.docket_filings_pending} of {r.docket_filings_total} pending
            </span>
          ) : null}
        </span>
        <button
          type="button"
          className="chip chip--clear"
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} details for ${r.file_number}`}
          onClick={(e) => {
            e.stopPropagation();
            setOpen(!open);
          }}
        >
          {open ? "hide ▴" : "details ▾"}
        </button>
      </div>
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
        <span className="num" title="Total satellites in the active constellation, per Schedule S">
          {r.spec_total_satellites} {r.spec_total_satellites === 1 ? "sat" : "sats"}
        </span>
      ) : null}
      <span className="num" title="Orbital planes listed in Schedule S">
        {r.spec_planes_n} {r.spec_planes_n === 1 ? "plane" : "planes"}
      </span>
      {alt ? (
        <span className="num" title="Mean altitude across planes, from filed apogee and perigee">
          {alt}
        </span>
      ) : null}
      {inc ? (
        <span className="num" title="Inclination range across planes, as filed">
          {inc}
        </span>
      ) : null}
      {r.spec_implausible_n ? (
        <span
          className="badge"
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
