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
  SatelliteDetail,
  SatelliteSummary,
  SearchResponse,
  Stats,
  StatusConflictRow,
  DecayConflictRow,
  StaleOwnerRow,
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
    },
    parents: [],
    children: [],
    fleet_by_status: {},
    fleet_by_regime: {},
    acquisitions: [],
    top_satellites: [],
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
