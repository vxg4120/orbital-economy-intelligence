import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  getReviewCase,
  getReviewCases,
  getReviewNext,
  getSatelliteTrack,
  submitVerdict,
  MOCK,
} from "../api/client";
import { ApiError } from "../api/client";
import type {
  Dossier,
  GoldSatelliteEvidence,
  LifeTrack as LifeTrackData,
  ReviewCaseDetail,
  Verdict,
} from "../api/types";
import { isCosparEvidence } from "../api/types";
import { useApi } from "../hooks/useApi";
import { SITE_NAME, useDocumentTitle } from "../seo";
import {
  NEEDS_CORRECTION,
  VERDICT_META,
  buildCanonicalMap,
  stratumLabel,
  verdictMeta,
} from "../lib/reviewStrata";
import { fmtDate, fmtDateTime } from "../lib/format";
import { guideFor } from "../lib/reviewGuides";
import { Panel } from "../components/Panel";
import { SourceBadge } from "../components/SourceBadge";
import { StatusBadge } from "../components/StatusBadge";
import { VerdictBadge } from "../components/VerdictBadge";
import { LIFETRACK_COMPACT_H, LifeTrack, LifeTrackFoot } from "../components/LifeTrack";
import { EmptyState, ErrorState, Loading } from "../components/States";
import { coarsePointer } from "../lib/pointer";

const TOKEN_KEY = "oei_review_token";

/** The queue's filter query, carried from the queue to a case and back. */
function queueQuery(type: string | undefined, only: string | undefined): string {
  const q = new URLSearchParams();
  if (type) q.set("type", type);
  if (only && only !== "unlabeled") q.set("only", only);
  const str = q.toString();
  return str ? `?${str}` : "";
}

/** Storage can throw (Safari with all cookies blocked, a sandboxed frame); the token is a
    convenience, so a blocked store reads as "no token" rather than a terminal fault. */
function readStoredToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}
function storeToken(tok: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, tok);
  } catch {
    /* the verdict is already stored; losing the convenience is fine */
  }
}
// Matches scripts/review.py, and the Space-Track feed's claim source is "spacetrack_decay".
const SOURCE_ORDER = ["satcat", "gcat", "ucs", "operator_seed", "spacetrack_decay", "resolve"];

/** The fields api/routers/review.py adds to a case detail to date it. They belong on
    ReviewCaseDetail in api/types.ts; until they move there they are optional here, so a mock
    fixture or an older API without them simply shows no snapshot note. */
export interface CaseSnapshot {
  drawn_at?: string | null;
  observed_through?: string | null;
  since_drawn?: { field: string; then: string | number | null; now: string | number | null }[];
}

const CHANGE_LABEL: Record<string, string> = {
  canonical_name: "name",
  status: "resolved status",
};

/** "the catalog has given this object NORAD 69869, its name has changed from SENTINEL-1 to
    NEXTSAT-4, and its resolved status has changed from DECAYED to ACTIVE". */
export function sinceDrawnSentence(changes: NonNullable<CaseSnapshot["since_drawn"]>): string {
  const clauses = changes.map((c) => {
    if (c.field === "record") return "the graph no longer holds this record";
    if (c.field === "norad_id") {
      if (c.then === null) return `the catalog has given this object NORAD ${c.now}`;
      if (c.now === null) return "this object no longer has a NORAD id";
      return `its NORAD id has changed from ${c.then} to ${c.now}`;
    }
    const label = CHANGE_LABEL[c.field] ?? c.field;
    if (c.then === null) return `its ${label} is now ${c.now}`;
    if (c.now === null) return `it no longer has a ${label}`;
    return `its ${label} has changed from ${c.then} to ${c.now}`;
  });
  const joined =
    clauses.length <= 2
      ? clauses.join(" and ")
      : `${clauses.slice(0, -1).join(", ")}, and ${clauses[clauses.length - 1]}`;
  return `Since this case was drawn, ${joined}.`;
}

/** The case is a snapshot, and a verdict grades that snapshot, so the stored question and answer
    are shown as drawn. This note dates them, and it says what the graph holds differently now. */
export function SnapshotNote({ detail }: { detail: ReviewCaseDetail & CaseSnapshot }) {
  if (!detail.drawn_at) return null;
  const changes = detail.since_drawn ?? [];
  return (
    <Panel title="Snapshot" meta={`drawn ${fmtDate(detail.drawn_at)}`}>
      <p className="hint">
        This case was drawn on {fmtDate(detail.drawn_at)}
        {detail.observed_through
          ? `, from source data observed up to ${fmtDate(detail.observed_through)},`
          : ","}{" "}
        and it shows the sources and the graph's answer as they stood then.
      </p>
      {changes.length > 0 ? <p className="hint audit-warn">{sinceDrawnSentence(changes)}</p> : null}
    </Panel>
  );
}
const ATTR_ORDER = ["name", "owner", "status", "object_type", "decay_date", "launch_date"];

export function ReviewCase() {
  const { caseId } = useParams<{ caseId: string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const type = params.get("type") ?? undefined;
  const only = params.get("only") ?? undefined;
  const id = caseId ? Number(caseId) : NaN;
  const idOk = Number.isFinite(id);

  const detail = useApi<ReviewCaseDetail | null>(
    () => (idOk ? getReviewCase(id) : Promise.resolve(null)),
    [id],
  );
  const subject = detail.data?.subject_ref;
  useDocumentTitle(subject ? `${subject} · Review · ${SITE_NAME}` : undefined);
  // The ordered unlabeled queue for this filter, for position (n of N) + j/k navigation.
  const queue = useApi(() => getReviewCases(type, "unlabeled", 500, 0), [type]);
  const queueIds = useMemo(() => (queue.data?.rows ?? []).map((r) => r.case_id), [queue.data]);

  const goTo = (nextId: number) =>
    navigate(`/review/${nextId}${type ? `?type=${type}` : ""}`);
  const queueHref = `/review${queueQuery(type, only)}`;
  const goQueue = () => navigate(queueHref);

  const idx = queueIds.indexOf(id);
  const goPrev = () => {
    if (idx > 0) goTo(queueIds[idx - 1]);
    else if (idx === -1 && queueIds.length) goTo(queueIds[queueIds.length - 1]);
  };
  const goNext = () => {
    if (idx >= 0 && idx < queueIds.length - 1) goTo(queueIds[idx + 1]);
    else if (idx === -1 && queueIds.length) goTo(queueIds[0]);
  };

  if (!idOk) return <EmptyState title="Not found" message={`No case “${caseId}”.`} />;

  const d = detail.data;
  return (
    <div className="view review-case fadein">
      {/* the head lives outside the case body, so Prev / Next survive the reload they fire: the
          button that was pressed keeps the keyboard's focus while the next case loads, and the
          plane keeps its top instead of blanking to a spinner */}
      <header className="rc-head">
        <div className="rc-head__main">
          {d ? (
            <>
              {/* the stratum is the page's chapter head (Fraunces, like every other route's h1)
                  and the subject reference rides inside it as a datum, so each case's heading
                  is its own (a heading list would otherwise repeat the stratum eight times) */}
              <h1 className="rc-head__title" data-stratum={d.case_type}>
                {stratumLabel(d.case_type)}{" "}
                <span className="rc-head__subject num">{d.subject_ref}</span>
              </h1>
              {d.verdict !== null ? <VerdictBadge verdict={d.verdict} /> : null}
            </>
          ) : (
            <span className="skeleton" style={{ width: 220 }} aria-hidden="true" />
          )}
        </div>
        <div className="rc-head__nav">
          <button className="btn" onClick={goPrev} title="Previous (k / ←)">
            ‹ Prev
          </button>
          <span className="rc-head__pos num">
            {idx >= 0 ? `${idx + 1} of ${queueIds.length}` : `${queueIds.length} unlabeled`}
          </span>
          <button className="btn" onClick={goNext} title="Next (j / →)">
            Next ›
          </button>
          <Link className="btn" to={queueHref}>
            Queue
          </Link>
        </div>
      </header>

      {detail.error ? (
        <ErrorState message={detail.error} onRetry={detail.reload} />
      ) : detail.loading && d === null ? (
        // a skeleton about the height of a case, so the verdict bar's slot does not collapse
        <Loading label="Loading case" rows={14} />
      ) : d ? (
        <CaseBody detail={d} type={type} onPrev={goPrev} onNext={goNext} onDone={goQueue} />
      ) : (
        <EmptyState title="Not found" message={`No case ${id}.`} />
      )}
    </div>
  );
}

function CaseBody({
  detail,
  type,
  onPrev,
  onNext,
  onDone,
}: {
  detail: ReviewCaseDetail;
  type: string | undefined;
  onPrev: () => void;
  onNext: () => void;
  onDone: () => void;
}) {
  const navigate = useNavigate();
  const ev = detail.evidence;
  const cluster = isCosparEvidence(ev);
  const guide = guideFor(detail.case_type);
  const dossier = detail.dossier ?? null;
  const alreadyLabeled = detail.verdict !== null;

  // Auto-show a compact LifeTrack when the case is about one satellite that has orbit history — a
  // status/decay case then becomes visually self-evident (plateau then break into decay).
  const track = useApi<LifeTrackData | null>(
    () =>
      detail.satellite_id !== null
        ? getSatelliteTrack(detail.satellite_id)
        : Promise.resolve(null),
    [detail.satellite_id],
  );
  const trackData = track.data && track.data.points.length > 0 ? track.data : null;

  // Pre-seed the verdict from the human's own past label if present, else from the AI suggestion.
  // The suggested-tag on the button (below) keeps it clear this is a recommendation, not a decision.
  const [verdict, setVerdict] = useState<Verdict | null>(
    detail.verdict ?? dossier?.recommended_verdict ?? null,
  );
  const [corrected, setCorrected] = useState(detail.corrected_answer ?? "");
  const [notes, setNotes] = useState(detail.verdict_notes ?? "");
  const [token, setToken] = useState(() => readStoredToken());
  const [needToken, setNeedToken] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);

  const notesRef = useRef<HTMLInputElement>(null);
  const correctedRef = useRef<HTMLInputElement>(null);
  const tokenRef = useRef<HTMLInputElement>(null);

  const needsCorrection = verdict !== null && NEEDS_CORRECTION.includes(verdict);

  async function doSubmit() {
    if (submitting) return;
    if (!verdict) {
      setErr(coarsePointer() ? "Pick a verdict." : "Pick a verdict — c / i / p / u.");
      return;
    }
    const tok = token.trim();
    if (!MOCK && !tok) {
      setNeedToken(true);
      setErr("Enter your review token to submit.");
      tokenRef.current?.focus();
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      await submitVerdict(
        detail.case_id,
        {
          verdict,
          corrected_answer: needsCorrection ? corrected.trim() || undefined : undefined,
          notes: notes.trim() || undefined,
          overwrite: alreadyLabeled,
        },
        tok,
      );
      if (!MOCK && tok) storeToken(tok);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Submit failed";
      setErr(msg);
      if (e instanceof ApiError && e.status === 401) {
        setNeedToken(true);
        tokenRef.current?.focus();
      }
      setSubmitting(false);
      return;
    }
    // The verdict is stored. Finding the next case is a separate request: if it fails, the
    // queue is the honest place to land, not a "Submit failed" over a label that was saved
    // (a retry would then send overwrite=false and get a 409).
    try {
      const { next_case_id } = await getReviewNext(type, detail.case_id);
      if (next_case_id !== null) {
        navigate(`/review/${next_case_id}${type ? `?type=${type}` : ""}`);
      } else {
        onDone();
      }
    } catch {
      onDone();
    } finally {
      setSubmitting(false);
    }
  }

  // Single global key handler, always calling the latest closure via a ref.
  const kbd = useRef<(e: KeyboardEvent) => void>(() => {});
  kbd.current = (e: KeyboardEvent) => {
    const el = document.activeElement as HTMLElement | null;
    const inInput = !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
    // Enter submits from the page body or the verdict bar's fields. On a focused button, link
    // or select it is that control's own activation (Next, Queue, the guide toggle, the nav),
    // which this handler used to swallow and turn into a submit.
    // A clicked verdict button keeps the focus in Chromium, and Enter on it would only re-select
    // the same verdict: that one control stays on the page's Enter-submit path.
    const onVerdict = !!el && el.classList.contains("verdict-btn");
    const onControl =
      !!el &&
      !onVerdict &&
      (el.tagName === "BUTTON" || el.tagName === "A" || el.tagName === "SELECT");
    if (e.key === "Enter") {
      if (onControl || e.isComposing) return;
      e.preventDefault();
      void doSubmit();
      return;
    }
    if (e.key === "Escape") {
      if (inInput) el!.blur();
      return;
    }
    if (inInput) return; // don't fire single-key shortcuts while typing
    const k = e.key.toLowerCase();
    if (k === "c") setVerdict("correct");
    else if (k === "i") setVerdict("incorrect");
    else if (k === "p") setVerdict("partial");
    else if (k === "u") setVerdict("unresolvable");
    else if (k === "n") {
      e.preventDefault();
      notesRef.current?.focus();
    } else if (k === "k" || e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      onPrev();
    } else if (k === "j" || e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      onNext();
    }
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => kbd.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // Focus the corrected-answer field the moment a correction-requiring verdict is chosen. Not
  // on mount: a dossier-seeded "incorrect" would otherwise put the caret in the input and
  // disable the c/i/p/u/j/k shortcuts the page advertises until Escape. Tracked as the previous
  // value rather than a mount flag: StrictMode replays the mount effect with refs preserved, and
  // a flag would then focus on mount after all.
  const wasCorrection = useRef(needsCorrection);
  useEffect(() => {
    if (needsCorrection && !wasCorrection.current) correctedRef.current?.focus();
    wasCorrection.current = needsCorrection;
  }, [needsCorrection]);

  const sats: GoldSatelliteEvidence[] = cluster ? ev.satellites : [ev];

  return (
    <>
      <div className="rc-grid">
        <SnapshotNote detail={detail} />
        {sats.map((s, i) => (
          <div className="rc-sat" key={s.satellite_id ?? i}>
            {cluster ? (
              <div className="rc-sat__title">
                <span className="mono-hi">{s.canonical_name ?? `object ${s.satellite_id}`}</span>
                <span className="hint num">
                  {s.norad_id !== null ? `NORAD ${s.norad_id}` : "no NORAD"} · {s.jcat ?? "—"}
                </span>
              </div>
            ) : null}
            <IdentityStrip ev={s} />
            <ClaimsGrid ev={s} />
          </div>
        ))}

        {track.loading && track.data === null && detail.satellite_id !== null ? (
          // The orbit history arrives after the case itself; hold its slot with the chart's own
          // block (a skeleton the chart's height over the real foot line), so the panels below do
          // not jump when it lands at any width. The counts on the right are not known yet.
          <Panel title="Life track" meta="resolving orbit history">
            <div className="lifetrack" aria-hidden="true">
              <div className="skeleton" style={{ height: LIFETRACK_COMPACT_H }} />
              <LifeTrackFoot
                right={(!cluster && ev.norad_id !== null ? `NORAD ${ev.norad_id} · ` : "") + "… d · … pts"}
              />
            </div>
          </Panel>
        ) : trackData ? (
          <Panel title="Life track" meta="daily orbit — physics vs the catalog">
            <LifeTrack data={trackData} variant="compact" />
          </Panel>
        ) : null}

        <div className="grid grid--2 rc-qa">
          <Panel title="The question" flush>
            <p className="rc-question">{detail.question}</p>
          </Panel>
          <Panel title="System answer" meta="what the graph resolved when drawn" flush>
            <p className="rc-answer">{detail.system_answer}</p>
            {!cluster ? <ResolvedLine ev={ev} /> : null}
          </Panel>
        </div>

        {guide ? (
          <div className={`rc-guide${guideOpen ? " is-open" : ""}`}>
            <button
              className="rc-guide__toggle"
              onClick={() => setGuideOpen((v) => !v)}
              aria-expanded={guideOpen}
            >
              <span className="rc-guide__chev" aria-hidden="true">
                {guideOpen ? "▾" : "▸"}
              </span>
              <span className="rc-guide__label">Arbitration guide</span>
              <span className="rc-guide__tldr">{guide.tldr}</span>
            </button>
            {guideOpen ? (
              <div className="rc-guide__body">
                {guide.paragraphs.map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {dossier ? (
          <DossierPanel dossier={dossier} />
        ) : (
          <p className="dossier-none">
            No AI research yet — a dossier appears here once a research agent finishes this case.
          </p>
        )}

        <Panel title="Research" meta="open sources in a new tab" flush>
          <ResearchLinks detail={detail} sats={sats} />
        </Panel>
      </div>

      <VerdictBar
        verdict={verdict}
        onVerdict={setVerdict}
        suggestedVerdict={alreadyLabeled ? null : (dossier?.recommended_verdict ?? null)}
        needsCorrection={needsCorrection}
        corrected={corrected}
        onCorrected={setCorrected}
        correctedRef={correctedRef}
        correctedPlaceholder={dossier?.recommended_answer ?? null}
        notes={notes}
        onNotes={setNotes}
        notesRef={notesRef}
        needToken={needToken && !MOCK}
        token={token}
        onToken={setToken}
        tokenRef={tokenRef}
        submitting={submitting}
        err={err}
        alreadyLabeled={alreadyLabeled}
        onSubmit={doSubmit}
      />
    </>
  );
}

/* ---- identity check strip ------------------------------------------------- */
function IdentityStrip({ ev }: { ev: GoldSatelliteEvidence }) {
  const bySource = useMemo(() => {
    const m = new Map<string, { id_type: string; id_value: string }[]>();
    for (const idr of ev.identifiers ?? []) {
      const arr = m.get(idr.source) ?? [];
      arr.push({ id_type: idr.id_type, id_value: idr.id_value });
      m.set(idr.source, arr);
    }
    return orderSources([...m.entries()]);
  }, [ev.identifiers]);

  return (
    <Panel title="Identity check" meta="do both catalogs describe one object?" flush>
      {bySource.length === 0 ? (
        <div className="idstrip idstrip--empty">
          <span className="hint">No identifiers recorded.</span>
        </div>
      ) : (
        <div className="idstrip">
          {bySource.map(([source, ids]) => (
            <div className="idstrip__src" key={source}>
              <SourceBadge source={source} />
              <div className="idstrip__ids">
                {ids.map((idr, i) => (
                  <span className="idstrip__id" key={`${idr.id_type}-${i}`}>
                    <span className="idstrip__type">{idr.id_type}</span>
                    <span className="idstrip__val num">{idr.id_value}</span>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/* ---- claims grid: attribute × source, conflicts highlighted --------------- */
function ClaimsGrid({ ev }: { ev: GoldSatelliteEvidence }) {
  const { sources, attrs, cell } = useMemo(() => {
    const byAttr = new Map<string, Map<string, string>>();
    const srcSet = new Set<string>();
    for (const a of ev.assertions ?? []) {
      srcSet.add(a.source);
      const row = byAttr.get(a.attribute) ?? new Map<string, string>();
      if (!row.has(a.source)) row.set(a.source, a.value); // first (latest — assertions are pre-sorted)
      byAttr.set(a.attribute, row);
    }
    const sources = orderSourcesList([...srcSet]);
    const attrs = [...byAttr.keys()].sort(
      (a, b) => attrRank(a) - attrRank(b) || a.localeCompare(b),
    );
    return { sources, attrs, cell: byAttr };
  }, [ev.assertions]);

  const canon = useMemo(() => buildCanonicalMap(ev), [ev]);

  if (attrs.length === 0) {
    return (
      <Panel title="Claims" flush>
        <div className="state state--sm">
          <span className="hint">No source assertions recorded.</span>
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="Claims" meta="raw value → canonical · conflicts flagged" flush>
      <div className="table-wrap">
        <table className="claims">
          <thead>
            <tr>
              <th className="claims__attr-h">attribute</th>
              {sources.map((s) => (
                <th key={s}>
                  <SourceBadge source={s} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {attrs.map((attr) => {
              const row = cell.get(attr)!;
              const values = sources.map((s) => row.get(s) ?? "");
              const distinct = new Set(values.filter((v) => v !== ""));
              const conflict = distinct.size > 1;
              return (
                <tr key={attr} className={conflict ? "is-conflict" : ""}>
                  <th className="claims__attr">
                    {attr}
                    {conflict ? <span className="claims__flag" aria-hidden="true" /> : null}
                  </th>
                  {sources.map((s) => {
                    const raw = row.get(s);
                    const c = canon.get(`${attr}|${s}`);
                    return (
                      <td key={s} className={conflict && raw ? "is-conflict" : ""}>
                        {raw ? (
                          <span className="claim">
                            <span className="claim__raw">{raw}</span>
                            {c ? <span className="claim__canon">→ {c}</span> : null}
                          </span>
                        ) : (
                          <span className="dash">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function ResolvedLine({ ev }: { ev: GoldSatelliteEvidence }) {
  const r = ev.resolved;
  if (!r) return null;
  return (
    <div className="rc-resolved">
      <span className="rc-resolved__k">resolved</span>
      <StatusBadge status={r.status} />
      <span className="rc-resolved__owner mono-hi">{r.owner ?? "unresolved owner"}</span>
    </div>
  );
}

/* ---- research links ------------------------------------------------------- */
function ResearchLinks({
  detail,
  sats,
}: {
  detail: ReviewCaseDetail;
  sats: GoldSatelliteEvidence[];
}) {
  return (
    <div className="research">
      {detail.satellite_id !== null ? (
        <Link className="research__link research__link--in" to={`/resolver/${detail.satellite_id}`}>
          <span className="research__site">Resolver</span>
          <span className="research__q">open /resolver/{detail.satellite_id}</span>
        </Link>
      ) : null}
      {sats.map((s, i) => (
        <SatLinks key={s.satellite_id ?? i} ev={s} showResolver={detail.satellite_id === null} />
      ))}
    </div>
  );
}

function SatLinks({ ev, showResolver }: { ev: GoldSatelliteEvidence; showResolver: boolean }) {
  const name = ev.canonical_name ?? "";
  const links: { site: string; q: string; href: string }[] = [];
  if (name) {
    links.push({
      site: "Google",
      q: `${name} satellite`,
      href: `https://www.google.com/search?q=${encodeURIComponent(name + " satellite")}`,
    });
  }
  if (ev.jcat) {
    // GCAT has no per-object page (verified: planet4589.org/.../S<jcat>.html 404s). Site-scoped
    // search on the JCAT id — GCAT's own primary key — is the working deep-link.
    links.push({
      site: "GCAT",
      q: `site:planet4589.org "${ev.jcat}"`,
      href: `https://www.google.com/search?q=${encodeURIComponent(`site:planet4589.org "${ev.jcat}"`)}`,
    });
  }
  if (ev.norad_id !== null) {
    links.push({
      site: "CelesTrak",
      q: `SATCAT ${ev.norad_id}`,
      href: `https://celestrak.org/satcat/records.php?CATNR=${ev.norad_id}`,
    });
  }
  return (
    <>
      {showResolver && ev.satellite_id != null ? (
        <Link className="research__link research__link--in" to={`/resolver/${ev.satellite_id}`}>
          <span className="research__site">Resolver</span>
          <span className="research__q">open /resolver/{ev.satellite_id}</span>
        </Link>
      ) : null}
      {links.map((l) => (
        <a className="research__link" key={l.site} href={l.href} target="_blank" rel="noreferrer">
          <span className="research__site">{l.site}</span>
          <span className="research__q">{l.q}</span>
        </a>
      ))}
    </>
  );
}

/* ---- AI research dossier --------------------------------------------------
   The newcomer explainer: what an agent found for THIS case and the verdict it recommends. The
   human stays the adjudicator — the recommendation chip is colored by the verdict (i.e. by whether
   the AI agrees the system answer is right), the summary gets comfortable reading typography, and
   every claim links out to its source. */
function DossierPanel({ dossier }: { dossier: Dossier }) {
  const rec = verdictMeta(dossier.recommended_verdict);
  return (
    <section className="dossier" aria-label="AI research dossier">
      <header className="dossier__head">
        <span className="dossier__eyebrow">AI Research</span>
        <div className="dossier__chips">
          <span
            className={`dossier__rec ${rec?.className ?? ""}`}
            title="Verdict the AI recommends — colored by whether it agrees with the system answer"
          >
            <span className="dossier__rec-glyph" aria-hidden="true" />
            <span className="dossier__rec-k">recommends</span>
            <span className="dossier__rec-v">{rec?.label ?? dossier.recommended_verdict}</span>
          </span>
          <span
            className={`dossier__conf dossier__conf--${dossier.confidence}`}
            title="How confident the research agent is"
          >
            {dossier.confidence} confidence
          </span>
        </div>
      </header>

      <p className="dossier__summary">{dossier.summary}</p>

      {dossier.evidence.length > 0 ? (
        <ul className="dossier__evidence">
          {dossier.evidence.map((e, i) => (
            <li className="dossier__ev" key={i}>
              <a
                className="dossier__ev-link"
                href={e.url}
                target="_blank"
                rel="noreferrer"
              >
                <span className="dossier__ev-source">{e.source_name}</span>
                <span className="dossier__ev-claim">{e.claim}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : null}

      {dossier.caveats ? <p className="dossier__caveats">{dossier.caveats}</p> : null}

      <div className="dossier__foot">
        researched {fmtDateTime(dossier.researched_at)}
        {dossier.agent_model ? ` · ${dossier.agent_model}` : ""}
      </div>
    </section>
  );
}

/* ---- sticky verdict bar --------------------------------------------------- */
function VerdictBar(props: {
  verdict: Verdict | null;
  onVerdict: (v: Verdict) => void;
  suggestedVerdict: Verdict | null;
  needsCorrection: boolean;
  corrected: string;
  onCorrected: (v: string) => void;
  correctedRef: React.RefObject<HTMLInputElement>;
  correctedPlaceholder: string | null;
  notes: string;
  onNotes: (v: string) => void;
  notesRef: React.RefObject<HTMLInputElement>;
  needToken: boolean;
  token: string;
  onToken: (v: string) => void;
  tokenRef: React.RefObject<HTMLInputElement>;
  submitting: boolean;
  err: string | null;
  alreadyLabeled: boolean;
  onSubmit: () => void;
}) {
  const coarse = coarsePointer();
  return (
    <div className="verdict-bar" role="form" aria-label="Record verdict">
      <div className="verdict-bar__inner">
        <div className="verdict-btns">
          {VERDICT_META.map((m) => (
            <button
              key={m.key}
              type="button"
              className={`verdict-btn ${m.className}${props.verdict === m.key ? " is-active" : ""}${
                props.suggestedVerdict === m.key ? " is-suggested" : ""
              }`}
              aria-pressed={props.verdict === m.key}
              onClick={() => props.onVerdict(m.key)}
            >
              <span className="verdict-btn__key">{m.short}</span>
              <span className="verdict-btn__label">{m.label}</span>
              {props.suggestedVerdict === m.key ? (
                <span className="verdict-btn__suggest" title="Pre-selected from AI research — override freely">
                  suggested
                </span>
              ) : null}
            </button>
          ))}
        </div>

        <div className="verdict-inputs">
          {props.needsCorrection ? (
            <input
              ref={props.correctedRef}
              className="vinput"
              value={props.corrected}
              onChange={(e) => props.onCorrected(e.target.value)}
              placeholder={
                props.correctedPlaceholder
                  ? `AI suggests: ${props.correctedPlaceholder}`
                  : "Corrected answer (what the truth actually is)"
              }
              aria-label="Corrected answer"
              autoComplete="off"
            />
          ) : null}
          <input
            ref={props.notesRef}
            className="vinput"
            value={props.notes}
            onChange={(e) => props.onNotes(e.target.value)}
            placeholder={coarse ? "Notes — sources, reasoning" : "Notes — sources, reasoning (press n)"}
            aria-label="Notes"
            autoComplete="off"
          />
          {props.needToken ? (
            <input
              ref={props.tokenRef}
              className="vinput vinput--token"
              value={props.token}
              onChange={(e) => props.onToken(e.target.value)}
              placeholder="X-Review-Token"
              aria-label="Review token"
              type="password"
              autoComplete="off"
            />
          ) : null}
        </div>

        <button
          className="btn btn--submit"
          onClick={props.onSubmit}
          aria-busy={props.submitting || undefined}
        >
          {props.submitting ? "Saving…" : props.alreadyLabeled ? "Update ⏎" : "Submit ⏎"}
        </button>
      </div>
      {/* only the error is live: a screen reader hears a submit error, and never the legend
          re-entering the region after one clears (the legend is outside it) */}
      <div className="verdict-bar__foot">
        <span className="verdict-bar__err" role="status" aria-live="polite">
          {props.err ?? ""}
        </span>
        {props.err ? null : (
          <span className="verdict-bar__hint">
            c/i/p/u verdict · n notes · Enter submit → auto-advance · j/k prev/next
          </span>
        )}
      </div>
    </div>
  );
}

/* ---- ordering helpers ----------------------------------------------------- */
function attrRank(attr: string): number {
  const i = ATTR_ORDER.indexOf(attr);
  return i < 0 ? ATTR_ORDER.length : i;
}
function sourceRank(source: string): number {
  const i = SOURCE_ORDER.indexOf(source);
  return i < 0 ? SOURCE_ORDER.length : i;
}
function orderSourcesList(sources: string[]): string[] {
  return [...sources].sort((a, b) => sourceRank(a) - sourceRank(b) || a.localeCompare(b));
}
function orderSources<T>(entries: [string, T][]): [string, T][] {
  return [...entries].sort((a, b) => sourceRank(a[0]) - sourceRank(b[0]) || a[0].localeCompare(b[0]));
}
