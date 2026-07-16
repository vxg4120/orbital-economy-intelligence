/* =============================================================================
   Two Skies bridge API (view 05). Live-only: the /passes forecaster SGP4-propagates
   the LEO catalog server-side, so there is no mock fixture — the view calls the real
   FastAPI service under /api/twoskies (same-origin in the built SPA, dev-proxied to
   :8600). Kept separate from the main client so the shared client/types stay untouched.
   ============================================================================= */

const BASE = "/api/twoskies";

export interface TwoSkyTarget {
  candidate: string;
  host: string;
  tic_id: number | null;
  ra_deg: number;
  dec_deg: number;
  disposition: string;
  has_conflict: boolean;
  n_candidates: number;
  category: "famous" | "conflict";
}

export interface TargetsResponse {
  targets: TwoSkyTarget[];
  n_famous: number;
  n_conflict: number;
  note: string;
}

export interface CongestionBin {
  alt_bin_km: number;
  inc_bin_deg: number;
  object_count: number;
}

export interface CongestionAstronomy {
  catalog_objects: number;
  tracked_with_elements: number;
  leo_objects: number;
  payloads_launched_1y: number;
  payloads_launched_30d: number;
  top_operators: { operator: string; payloads: number }[];
  shells: { alt_lo_km: number; alt_hi_km: number; objects: number }[];
  peak_bin: CongestionBin;
  bins: CongestionBin[];
  caveats: string[];
  note: string;
}

export interface SatPass {
  norad: number;
  name: string;
  operator: string | null;
  alt_km: number;
  closest_sep_deg: number;
  alt_deg: number;
  time_utc: string;
}

export interface PassesResponse {
  target: { ra_deg: number; dec_deg: number };
  site: { key: string | null; name: string; lat_deg: number; lon_deg: number; elev_km: number };
  window: { start_utc: string; end_utc: string; window_min: number; step_sec: number; steps: number };
  sep_deg: number;
  min_alt_deg: number;
  target_visible: boolean;
  target_max_alt_deg: number;
  n_considered: number;
  n_found: number;
  truncated: boolean;
  passes: SatPass[];
  operator_tally: { operator: string; count: number }[];
  elapsed_ms: number;
  caveats: string[];
}

export interface SiteOption {
  key: string;
  name: string;
}

/** The observatory presets mirror api/routers/twoskies.SITES (real ground-based follow-up sites). */
export const SITES: SiteOption[] = [
  { key: "kitt_peak", name: "Kitt Peak, Arizona (TFOP)" },
  { key: "paranal", name: "Cerro Paranal / VLT, Chile" },
  { key: "mauna_kea", name: "Maunakea, Hawaii" },
  { key: "la_palma", name: "Roque de los Muchachos, La Palma" },
  { key: "siding_spring", name: "Siding Spring, Australia (TFOP)" },
  { key: "sutherland", name: "SAAO Sutherland, South Africa" },
  { key: "generic_north", name: "Generic mid-northern site (35N)" },
];

async function get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const qs = params
    ? "?" + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString()
    : "";
  const res = await fetch(`${BASE}${path}${qs}`, { headers: { accept: "application/json" } });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function getTargets(): Promise<TargetsResponse> {
  return get<TargetsResponse>("/targets");
}

export function getCongestionAstronomy(): Promise<CongestionAstronomy> {
  return get<CongestionAstronomy>("/congestion-astronomy");
}

export interface PassQuery {
  ra: number;
  dec: number;
  datetime?: string;
  site?: string;
  window_min?: number;
  sep_deg?: number;
  min_alt_deg?: number;
}

export function getPasses(q: PassQuery): Promise<PassesResponse> {
  const params: Record<string, string | number> = { ra: q.ra, dec: q.dec };
  if (q.datetime) params.datetime = q.datetime;
  if (q.site) params.site = q.site;
  if (q.window_min !== undefined) params.window_min = q.window_min;
  if (q.sep_deg !== undefined) params.sep_deg = q.sep_deg;
  if (q.min_alt_deg !== undefined) params.min_alt_deg = q.min_alt_deg;
  return get<PassesResponse>("/passes", params);
}
