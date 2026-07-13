/* =============================================================================
   API DTOs — transcribed verbatim from the spec's "API contract (v1)" section
   (docs/superpowers/specs/2026-07-08-frontend-design.md). This file is the ONLY
   source of API shapes for the SPA; views never invent ad-hoc shapes. Field
   names must match the contract exactly — the API (Task F1) produces these.

   Dates arrive as ISO strings ("YYYY-MM-DD" for DATE, RFC3339 for TIMESTAMPTZ)
   or null. NORAD ids are BIGINT and may be null (analyst objects) — every view
   renders null gracefully.
   ============================================================================= */

export type CanonicalStatus =
  | "ACTIVE"
  | "PARTIAL"
  | "SPARE"
  | "INACTIVE"
  | "GRAVEYARD"
  | "DECAYED"
  | "UNKNOWN";

export type ObjectType = "PAYLOAD" | "ROCKET_BODY" | "DEBRIS" | "UNKNOWN";

/* ---- GET /api/stats ---- */
export interface IngestRun {
  source: string;
  endpoint: string;
  // ok | skipped_fresh | error | running | stale — and null for a row still in flight (the API
  // normalizes NULL to running/stale, but the type stays nullable so the SPA never trusts it).
  status: string | null;
  finished_at: string | null;
  rows_ingested: number | null;
}

export interface Stats {
  satellites: number;
  on_orbit_payloads: number;
  operators: number;
  identifier_rows: number;
  merge_events: number;
  gp_elements: number;
  coverage: {
    operator_pct: number;
    status_pct: number;
    multi_source_pct: number;
  };
  conflicts: {
    status: number;
    decay: number;
    stale_owners: number;
  };
  ingest_runs: IngestRun[];
}

/* ---- GET /api/satellites/search ---- */
export interface SatelliteSummary {
  satellite_id: number;
  norad_id: number | null;
  cospar_id: string | null;
  canonical_name: string;
  object_type: ObjectType | string;
  launch_date: string | null;
  decay_date: string | null;
  operator_name: string | null;
  canonical_status: CanonicalStatus | string | null;
}

export interface SearchResponse {
  results: SatelliteSummary[];
}

/* ---- GET /api/satellites/{id} ---- */
export interface Identifier {
  id_type: string;
  id_value: string;
  source: string;
  confidence: number;
  valid_from: string | null;
  valid_to: string | null;
}

export interface OwnershipSegment {
  operator_id: number;
  operator_name: string;
  role: string; // owner | operator | manufacturer
  valid_from: string | null;
  valid_to: string | null;
  source: string;
  confidence: number;
}

export interface StatusEvent {
  canonical_status: CanonicalStatus | string;
  observed_at: string;
  source: string;
}

export interface Assertion {
  attribute: string;
  value: string;
  source: string;
  observed_at: string;
}

export interface LatestElements {
  epoch: string;
  semi_major_axis_km: number | null;
  apogee_km: number | null;
  perigee_km: number | null;
  inclination: number | null;
  eccentricity: number | null;
  mean_motion: number;
}

export interface MergeEvent {
  rule_fired: string;
  score: number | null;
  merged_at: string;
  details: Record<string, unknown> | null;
}

export interface SatelliteDetail {
  satellite: SatelliteSummary;
  identifiers: Identifier[];
  ownership: OwnershipSegment[];
  status_history: StatusEvent[];
  assertions: Assertion[];
  conflicts: string[]; // attributes with cross-source disagreement
  latest_elements: LatestElements | null;
  merge_events: MergeEvent[];
}

/* ---- GET /api/conflicts/* ---- */
export interface Paginated<Row> {
  rows: Row[];
  total: number;
}

export interface StatusConflictRow {
  satellite_id: number;
  norad_id: number | null;
  canonical_name: string;
  satcat_status: string;
  gcat_status: string;
}

export interface DecayConflictRow {
  satellite_id: number;
  norad_id: number | null;
  canonical_name: string;
  sources_and_dates: string;
}

export interface StaleOwnerRow {
  satellite_id: number;
  norad_id: number | null;
  canonical_name: string;
  catalog_owner: string;
  resolved_operator: string;
  acquired_by: string;
  acquisition_date: string | null;
}

/* ---- GET /api/operators ---- */
export interface OperatorRow {
  operator_id: number;
  canonical_name: string;
  country: string | null;
  operator_class: string | null;
  parent_name: string | null;
  fleet_total: number;
  fleet_on_orbit: number;
  fleet_active: number;
}

export type OperatorsResponse = Paginated<OperatorRow>;
export type OperatorSort = "fleet" | "active" | "name";

/* ---- GET /api/operators/{id} ---- */
export interface OperatorRef {
  operator_id: number;
  canonical_name: string;
  relationship?: string;
  valid_from?: string | null;
  valid_to?: string | null;
}

export interface Acquisition {
  child?: string;
  parent?: string;
  relationship: string;
  valid_from: string | null;
  valid_to: string | null;
}

/* Fleet-sample rows from GET /api/operators/{id}: a reduced satellite summary —
   the API omits launch_date, decay_date and operator_name in this context. */
export type FleetSatellite = Pick<
  SatelliteSummary,
  "satellite_id" | "norad_id" | "cospar_id" | "canonical_name" | "object_type" | "canonical_status"
>;

export interface OperatorDetail {
  // The API returns the operator header plus its current fleet counts (the spec
  // leaves the sub-shape open; these three are always present — reconciled in F3).
  operator: {
    operator_id: number;
    canonical_name: string;
    country: string | null;
    operator_class: string | null;
    fleet_total: number;
    fleet_on_orbit: number;
    fleet_active: number;
  };
  parents: OperatorRef[];
  children: OperatorRef[];
  fleet_by_status: Record<string, number>;
  fleet_by_regime: Record<string, number>;
  acquisitions: Acquisition[];
  top_satellites: FleetSatellite[];
}

/* ---- GET /api/congestion ---- */
export interface CongestionBin {
  alt_bin_km: number;
  inc_bin_deg: number;
  object_count: number;
}

export interface CongestionResponse {
  bins: CongestionBin[];
}

/* =============================================================================
   Review area — the gold_case arbitration workbench (api/routers/review.py).
   The read DTOs mirror the router's responses; the evidence shape mirrors the
   packet scripts/build_gold_queue.py writes into gold_case.evidence (JSONB).
   ============================================================================= */

export type Verdict = "correct" | "incorrect" | "partial" | "unresolvable";

/* ---- GET /api/review/stats ---- */
export interface ReviewStratum {
  case_type: string;
  total: number;
  labeled: number;
  correct: number;
  incorrect: number;
  partial: number;
  unresolvable: number;
}

export interface ReviewStats {
  strata: ReviewStratum[];
  overall: ReviewStratum & { total: number; labeled: number };
  accuracy_so_far: number | null;
}

/* ---- GET /api/review/cases ---- */
export type ReviewOnly = "unlabeled" | "labeled" | "all";

export interface ReviewCaseRow {
  case_id: number;
  case_type: string;
  subject_ref: string;
  question: string;
  verdict: Verdict | null;
  labeled_at: string | null;
}

export interface ReviewCasesResponse {
  rows: ReviewCaseRow[];
  total: number;
}

/* ---- GET /api/review/next ---- */
export interface ReviewNextResponse {
  next_case_id: number | null;
}

/* ---- evidence JSONB (per stratum; see build_gold_queue.satellite_evidence) ---- */
export interface GoldAssertion {
  attribute: string;
  value: string;
  source: string;
  observed_at: string | null;
}

export interface GoldIdentifier {
  id_type: string;
  id_value: string;
  source: string;
  confidence: number;
}

export interface GoldOwnerSegment {
  operator: string | null;
  role: string;
  valid_from: string | null;
  valid_to: string | null;
  source: string;
}

export interface GoldResolved {
  status: string | null;
  status_source: string | null;
  owner: string | null;
  owner_history: GoldOwnerSegment[];
}

/** One satellite's full evidence packet. The stratum-specific conflict blocks are optional and
    carry the already-canonicalized values (e.g. status_conflict.satcat_canonical). */
export interface GoldSatelliteEvidence {
  satellite_id: number;
  norad_id: number | null;
  cospar_id: string | null;
  jcat: string | null;
  canonical_name: string | null;
  object_type: string | null;
  launch_date: string | null;
  decay_date: string | null;
  perigee_km: number | null;
  apogee_km: number | null;
  orbital_regime: string | null;
  identifiers: GoldIdentifier[];
  assertions: GoldAssertion[];
  resolved: GoldResolved;
  owner_dispute?: Record<string, unknown>;
  status_conflict?: Record<string, unknown>;
  decay_conflict?: Record<string, unknown>;
  type_conflict?: Record<string, unknown>;
  stale_owner?: Record<string, unknown>;
  missed_join?: Record<string, unknown> & { candidate?: GoldSatelliteEvidence };
}

/** ambiguous_cospar cases carry no single satellite — they hold the cluster instead. */
export interface GoldCosparEvidence {
  cospar: string;
  n_satellites: number;
  satellites: GoldSatelliteEvidence[];
}

export type GoldEvidence = GoldSatelliteEvidence | GoldCosparEvidence;

export function isCosparEvidence(ev: GoldEvidence): ev is GoldCosparEvidence {
  return Array.isArray((ev as GoldCosparEvidence).satellites);
}

/* ---- GET /api/review/cases/{id} ---- */
export interface ReviewCaseDetail {
  case_id: number;
  case_type: string;
  satellite_id: number | null;
  subject_ref: string;
  question: string;
  system_answer: string;
  evidence: GoldEvidence;
  verdict: Verdict | null;
  corrected_answer: string | null;
  verdict_notes: string | null;
  labeled_at: string | null;
}

/* ---- POST /api/review/cases/{id}/verdict ---- */
export interface VerdictSubmit {
  verdict: Verdict;
  corrected_answer?: string;
  notes?: string;
  overwrite?: boolean;
}

export interface VerdictResponse {
  ok: boolean;
  verdict: {
    case_type: string;
    subject_ref: string;
    verdict: Verdict;
    corrected_answer: string | null;
    verdict_notes: string | null;
    labeled_at: string | null;
  };
}
