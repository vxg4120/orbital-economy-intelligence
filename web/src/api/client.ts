/* =============================================================================
   API client. Real mode: fetch JSON from the FastAPI service under /api
   (dev-proxied to :8600, or same-origin when the SPA is served from web/dist).
   Mock mode (VITE_API_MOCK=1): resolve every request from the JSON fixtures in
   ./fixtures — no network — so the SPA is fully explorable before the API is up.

   The mock router mirrors the spec's query semantics (search matching, LIMIT/
   OFFSET pagination + total, id lookup) so views exercise the same code paths.
   ============================================================================= */

import type {
  CongestionResponse,
  OperatorDetail,
  OperatorRow,
  OperatorSort,
  OperatorsResponse,
  Paginated,
  ReviewCaseDetail,
  ReviewCaseRow,
  ReviewCasesResponse,
  ReviewNextResponse,
  ReviewOnly,
  ReviewStats,
  ReviewStratum,
  SatelliteDetail,
  SatelliteSummary,
  SearchResponse,
  Stats,
  StatusConflictRow,
  DecayConflictRow,
  StaleOwnerRow,
  Verdict,
  VerdictResponse,
  VerdictSubmit,
} from "./types";

import statsFixture from "./fixtures/stats.json";
import searchFixture from "./fixtures/search.json";
import satelliteFixture from "./fixtures/satellite.json";
import conflictsStatusFixture from "./fixtures/conflicts_status.json";
import conflictsDecayFixture from "./fixtures/conflicts_decay.json";
import conflictsStaleFixture from "./fixtures/conflicts_stale.json";
import operatorsFixture from "./fixtures/operators.json";
import operatorDetailFixture from "./fixtures/operator_detail.json";
import congestionFixture from "./fixtures/congestion.json";
import reviewCasesFixture from "./fixtures/review_cases.json";

export const MOCK = import.meta.env.VITE_API_MOCK === "1";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/* ---- real transport ------------------------------------------------------- */
async function realGet<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const qs = params
    ? "?" +
      new URLSearchParams(
        Object.entries(params).map(([k, v]) => [k, String(v)]),
      ).toString()
    : "";
  const res = await fetch(`/api${path}${qs}`, {
    headers: { accept: "application/json" },
  });
  if (!res.ok) {
    throw new ApiError(`${res.status} ${res.statusText} for ${path}`, res.status);
  }
  return (await res.json()) as T;
}

/** POST JSON with optional extra headers (the review-token header). Errors carry the status so the
    caller can distinguish 401 (bad token) / 409 (already labeled) from a generic failure. */
async function realPost<T>(
  path: string,
  body: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json", ...headers },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

/* ---- mock transport ------------------------------------------------------- */
const MOCK_LATENCY_MS = 140;
const delay = <T>(value: T): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(value), MOCK_LATENCY_MS));

const searchRows = (searchFixture as SearchResponse).results;
const satelliteMap = satelliteFixture as unknown as Record<string, SatelliteDetail>;
const operatorRows = (operatorsFixture as OperatorsResponse).rows;
const operatorDetailMap = operatorDetailFixture as unknown as Record<string, OperatorDetail>;

const COSPAR_RE = /^\d{4}-\d{3}[a-z]{1,3}$/i;

function mockSearch(q: string): SearchResponse {
  const query = q.trim();
  if (!query) return { results: [] };
  let hits: SatelliteSummary[];
  if (/^\d+$/.test(query)) {
    hits = searchRows.filter((r) => r.norad_id !== null && String(r.norad_id) === query);
  } else if (COSPAR_RE.test(query)) {
    hits = searchRows.filter((r) => (r.cospar_id ?? "").toLowerCase() === query.toLowerCase());
  } else {
    const lower = query.toLowerCase();
    hits = searchRows
      .filter((r) => r.canonical_name.toLowerCase().includes(lower))
      .sort((a, b) => {
        const ap = a.canonical_name.toLowerCase().startsWith(lower) ? 0 : 1;
        const bp = b.canonical_name.toLowerCase().startsWith(lower) ? 0 : 1;
        return ap - bp;
      });
  }
  return { results: hits.slice(0, 20) };
}

function synthDetail(id: number): SatelliteDetail {
  const row = searchRows.find((r) => r.satellite_id === id);
  const satellite: SatelliteSummary = row ?? {
    satellite_id: id,
    norad_id: null,
    cospar_id: null,
    canonical_name: `OBJECT ${id}`,
    object_type: "UNKNOWN",
    launch_date: null,
    decay_date: null,
    operator_name: null,
    canonical_status: "UNKNOWN",
  };
  return {
    satellite,
    identifiers: [],
    ownership: [],
    status_history: [],
    assertions: [],
    conflicts: [],
    latest_elements: null,
    merge_events: [],
  };
}

function paginate<Row>(all: Row[], total: number, limit: number, offset: number): Paginated<Row> {
  return { rows: all.slice(offset, offset + limit), total };
}

function sortOperators(rows: OperatorRow[], sort: OperatorSort): OperatorRow[] {
  const copy = [...rows];
  if (sort === "active") copy.sort((a, b) => b.fleet_active - a.fleet_active);
  else if (sort === "name") copy.sort((a, b) => a.canonical_name.localeCompare(b.canonical_name));
  else copy.sort((a, b) => b.fleet_total - a.fleet_total);
  return copy;
}

function synthOperatorDetail(id: number): OperatorDetail {
  const row = operatorRows.find((r) => r.operator_id === id);
  return {
    operator: {
      operator_id: id,
      canonical_name: row?.canonical_name ?? `OPERATOR ${id}`,
      country: row?.country ?? null,
      operator_class: row?.operator_class ?? null,
      fleet_total: row?.fleet_total ?? 0,
      fleet_on_orbit: row?.fleet_on_orbit ?? 0,
      fleet_active: row?.fleet_active ?? 0,
    },
    parents: [],
    children: [],
    fleet_by_status: {},
    fleet_by_regime: {},
    acquisitions: [],
    top_satellites: [],
  };
}

/* ---- mock review store ---------------------------------------------------- */
// A mutable in-memory copy of the fixtures so labeling "sticks" for a mock session: progress
// meters climb, accuracy updates, and auto-advance walks the queue exactly like the real API.
const reviewStore: ReviewCaseDetail[] = structuredClone(
  reviewCasesFixture as unknown as ReviewCaseDetail[],
);

const VERDICTS: Verdict[] = ["correct", "incorrect", "partial", "unresolvable"];
const STRATUM_ORDER = [
  "ambiguous_cospar", "rideshare_orphan", "missed_join_candidate", "owner_dispute",
  "status_conflict", "decay_conflict", "type_conflict", "stale_owner",
];

const byStableOrder = (a: ReviewCaseDetail, b: ReviewCaseDetail) =>
  a.case_type < b.case_type ? -1 : a.case_type > b.case_type ? 1 : a.case_id - b.case_id;

function mockReviewStats(): ReviewStats {
  const tally = new Map<string, Record<string, number>>();
  for (const c of reviewStore) {
    const row = tally.get(c.case_type) ?? { total: 0 };
    row.total += 1;
    if (c.verdict) row[c.verdict] = (row[c.verdict] ?? 0) + 1;
    tally.set(c.case_type, row);
  }
  const types = [...tally.keys()].sort((a, b) => {
    const ia = STRATUM_ORDER.indexOf(a);
    const ib = STRATUM_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || (a < b ? -1 : 1);
  });
  const agg: Record<string, number> = { correct: 0, incorrect: 0, partial: 0, unresolvable: 0 };
  let aggTotal = 0;
  const strata: ReviewStratum[] = types.map((t) => {
    const row = tally.get(t)!;
    const counts = Object.fromEntries(VERDICTS.map((v) => [v, row[v] ?? 0])) as Record<Verdict, number>;
    const labeled = VERDICTS.reduce((n, v) => n + counts[v], 0);
    for (const v of VERDICTS) agg[v] += counts[v];
    aggTotal += row.total;
    return { case_type: t, total: row.total, labeled, ...counts };
  });
  const labeled = VERDICTS.reduce((n, v) => n + agg[v], 0);
  const gradable = labeled - agg.unresolvable;
  return {
    strata,
    overall: { case_type: "overall", total: aggTotal, labeled, ...(agg as Record<Verdict, number>) },
    accuracy_so_far: gradable > 0 ? (agg.correct + 0.5 * agg.partial) / gradable : null,
  };
}

function mockReviewCases(
  type: string | undefined,
  only: ReviewOnly,
  limit: number,
  offset: number,
): ReviewCasesResponse {
  const filtered = reviewStore
    .filter((c) => (type ? c.case_type === type : true))
    .filter((c) =>
      only === "unlabeled" ? c.verdict === null : only === "labeled" ? c.verdict !== null : true,
    )
    .sort(byStableOrder);
  const rows: ReviewCaseRow[] = filtered.slice(offset, offset + limit).map((c) => ({
    case_id: c.case_id,
    case_type: c.case_type,
    subject_ref: c.subject_ref,
    question: c.question,
    verdict: c.verdict,
    labeled_at: c.labeled_at,
  }));
  return { rows, total: filtered.length };
}

function mockReviewNext(type: string | undefined, afterCaseId: number | undefined): ReviewNextResponse {
  const unlabeled = reviewStore
    .filter((c) => (type ? c.case_type === type : true))
    .filter((c) => c.verdict === null)
    .sort(byStableOrder);
  if (afterCaseId !== undefined) {
    const ref = reviewStore.find((c) => c.case_id === afterCaseId);
    if (ref) {
      const after = unlabeled.find(
        (c) => c.case_type > ref.case_type || (c.case_type === ref.case_type && c.case_id > ref.case_id),
      );
      if (after) return { next_case_id: after.case_id };
    }
  }
  return { next_case_id: unlabeled.length ? unlabeled[0].case_id : null };
}

function mockSubmitVerdict(id: number, body: VerdictSubmit): VerdictResponse {
  const c = reviewStore.find((x) => x.case_id === id);
  if (!c) throw new ApiError("case not found", 404);
  if (c.verdict !== null && !body.overwrite) throw new ApiError("case already labeled", 409);
  c.verdict = body.verdict;
  c.corrected_answer = body.corrected_answer ?? null;
  c.verdict_notes = body.notes ?? null;
  c.labeled_at = new Date().toISOString();
  return {
    ok: true,
    verdict: {
      case_type: c.case_type,
      subject_ref: c.subject_ref,
      verdict: c.verdict,
      corrected_answer: c.corrected_answer,
      verdict_notes: c.verdict_notes,
      labeled_at: c.labeled_at,
    },
  };
}

/* ---- public API ----------------------------------------------------------- */
export function getStats(): Promise<Stats> {
  if (MOCK) return delay(statsFixture as Stats);
  return realGet<Stats>("/stats");
}

export function searchSatellites(q: string): Promise<SearchResponse> {
  if (MOCK) return delay(mockSearch(q));
  return realGet<SearchResponse>("/satellites/search", { q });
}

export function getSatellite(id: number): Promise<SatelliteDetail> {
  if (MOCK) return delay(satelliteMap[String(id)] ?? synthDetail(id));
  return realGet<SatelliteDetail>(`/satellites/${id}`);
}

export function getConflictsStatus(limit: number, offset: number): Promise<Paginated<StatusConflictRow>> {
  if (MOCK) {
    const f = conflictsStatusFixture as Paginated<StatusConflictRow>;
    return delay(paginate(f.rows, f.total, limit, offset));
  }
  return realGet<Paginated<StatusConflictRow>>("/conflicts/status", { limit, offset });
}

export function getConflictsDecay(limit: number, offset: number): Promise<Paginated<DecayConflictRow>> {
  if (MOCK) {
    const f = conflictsDecayFixture as Paginated<DecayConflictRow>;
    return delay(paginate(f.rows, f.total, limit, offset));
  }
  return realGet<Paginated<DecayConflictRow>>("/conflicts/decay", { limit, offset });
}

export function getConflictsStale(limit: number, offset: number): Promise<Paginated<StaleOwnerRow>> {
  if (MOCK) {
    const f = conflictsStaleFixture as Paginated<StaleOwnerRow>;
    return delay(paginate(f.rows, f.total, limit, offset));
  }
  return realGet<Paginated<StaleOwnerRow>>("/conflicts/stale-owners", { limit, offset });
}

export function getOperators(limit: number, offset: number, sort: OperatorSort): Promise<OperatorsResponse> {
  if (MOCK) {
    const sorted = sortOperators(operatorRows, sort);
    return delay(paginate(sorted, (operatorsFixture as OperatorsResponse).total, limit, offset));
  }
  return realGet<OperatorsResponse>("/operators", { limit, offset, sort });
}

export function getOperator(id: number): Promise<OperatorDetail> {
  if (MOCK) return delay(operatorDetailMap[String(id)] ?? synthOperatorDetail(id));
  return realGet<OperatorDetail>(`/operators/${id}`);
}

export function getCongestion(): Promise<CongestionResponse> {
  if (MOCK) return delay(congestionFixture as CongestionResponse);
  return realGet<CongestionResponse>("/congestion");
}

/* ---- review area ---------------------------------------------------------- */
export function getReviewStats(): Promise<ReviewStats> {
  if (MOCK) return delay(mockReviewStats());
  return realGet<ReviewStats>("/review/stats");
}

export function getReviewCases(
  type: string | undefined,
  only: ReviewOnly,
  limit: number,
  offset: number,
): Promise<ReviewCasesResponse> {
  if (MOCK) return delay(mockReviewCases(type, only, limit, offset));
  const params: Record<string, string | number> = { only, limit, offset };
  if (type) params.type = type;
  return realGet<ReviewCasesResponse>("/review/cases", params);
}

export function getReviewCase(id: number): Promise<ReviewCaseDetail> {
  if (MOCK) {
    const c = reviewStore.find((x) => x.case_id === id);
    if (!c) return Promise.reject(new ApiError("case not found", 404));
    return delay(structuredClone(c));
  }
  return realGet<ReviewCaseDetail>(`/review/cases/${id}`);
}

export function getReviewNext(
  type?: string,
  afterCaseId?: number,
): Promise<ReviewNextResponse> {
  if (MOCK) return delay(mockReviewNext(type, afterCaseId));
  const params: Record<string, string | number> = {};
  if (type) params.type = type;
  if (afterCaseId !== undefined) params.after_case_id = afterCaseId;
  return realGet<ReviewNextResponse>("/review/next", params);
}

export function submitVerdict(
  id: number,
  body: VerdictSubmit,
  token: string,
): Promise<VerdictResponse> {
  if (MOCK) {
    try {
      return delay(mockSubmitVerdict(id, body));
    } catch (e) {
      return Promise.reject(e);
    }
  }
  return realPost<VerdictResponse>(`/review/cases/${id}/verdict`, body, {
    "X-Review-Token": token,
  });
}
