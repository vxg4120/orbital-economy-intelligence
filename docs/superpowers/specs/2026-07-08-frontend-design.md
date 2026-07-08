# Frontend Design — "Orbital Economy Terminal" (v1)

Approved 2026-07-08 (stack: FastAPI + React SPA; scope: all four views).

## Purpose

Portfolio-grade demo surface over the existing identity graph + metrics DB. Read-only. The FE is
the interview demo: it must make the entity-resolution layer *visible* — crosswalks, provenance,
temporal ownership, and conflicts — not just chart totals. Local-only for now (no deploy); ships
screenshots into the README.

## Architecture

```
web/  (Vite + React + TS SPA)  --dev proxy-->  api/  (FastAPI, read-only)  -->  Postgres/Timescale
```

- `api/`: FastAPI app (`api/main.py`, routers per domain in `api/routers/`), psycopg via the
  existing `common/db.py`. All queries read-only, parameterized, LIMIT-bounded. Uvicorn on port
  **8600**. When `web/dist` exists, serve it statically at `/` (single-process demo mode).
- `web/`: Vite + React + TypeScript, pnpm. React Router for the four views; fetch-based API
  client with shared DTO types in `web/src/api/types.ts` (mirrors the API contract below).
  Charts: Recharts; the congestion heatmap is a hand-rolled SVG grid. Dev server proxies
  `/api` → `localhost:8600`.
- New Python deps: `fastapi`, `uvicorn`, `httpx` (test client transport). Makefile targets:
  `api` (uvicorn), `web-dev`, `web-build`, `fe` (api + built SPA).

## Visual direction

Dark mission-control terminal: near-black background, monospace-forward type (system mono stack),
high-density tables, thin rules, one accent color for interactive elements plus a small
semantic palette for status (ACTIVE green, INACTIVE gray, DECAYED dim, conflict amber). Sparse,
precise, no cards-with-drop-shadows. Status/source values render as small mono badges. The
design should read as an instrument, not a marketing site.

## API contract (v1)

- `GET /api/stats` → `{ satellites, on_orbit_payloads, operators, identifier_rows, merge_events,
  gp_elements, coverage: {operator_pct, status_pct, multi_source_pct}, conflicts: {status,
  decay, stale_owners}, ingest_runs: [{source, endpoint, status, finished_at, rows_ingested}] }`
- `GET /api/satellites/search?q=` → `{ results: [{satellite_id, norad_id, cospar_id,
  canonical_name, object_type, launch_date, decay_date, operator_name, canonical_status}] }`
  (top 20; q matches name ILIKE, exact NORAD when numeric, COSPAR pattern)
- `GET /api/satellites/{id}` → `{ satellite: {...}, identifiers: [{id_type, id_value, source,
  confidence, valid_from, valid_to}], ownership: [{operator_id, operator_name, role, valid_from,
  valid_to, source, confidence}], status_history: [{canonical_status, observed_at, source}],
  assertions: [{attribute, value, source, observed_at}], conflicts: [attribute, ...],
  latest_elements: {epoch, semi_major_axis_km, apogee_km, perigee_km, inclination,
  eccentricity, mean_motion} | null, merge_events: [{rule_fired, score, merged_at, details}] }`
- `GET /api/conflicts/status?limit=50&offset=0` → rows `{satellite_id, norad_id, canonical_name,
  satcat_status, gcat_status}` + `total`
- `GET /api/conflicts/decay?limit&offset` → rows `{satellite_id, norad_id, canonical_name,
  sources_and_dates}` + `total`
- `GET /api/conflicts/stale-owners?limit&offset` → rows `{satellite_id, norad_id, canonical_name,
  catalog_owner, resolved_operator, acquired_by, acquisition_date}` + `total`
- `GET /api/operators?limit=100&offset=0&sort=fleet` → rows `{operator_id, canonical_name,
  country, operator_class, parent_name, fleet_total, fleet_on_orbit, fleet_active}` + `total`
- `GET /api/operators/{id}` → `{ operator: {...}, parents: [...], children: [...],
  fleet_by_status: {...}, fleet_by_regime: {...}, acquisitions: [{child|parent, relationship,
  valid_from, valid_to}], top_satellites: [...] }`
- `GET /api/congestion` → `{ bins: [{alt_bin_km, inc_bin_deg, object_count}] }` (LEO focus,
  50 km × 5° bins from latest elements)

Errors: 404 for unknown ids, 422 (FastAPI default) for bad params. No auth (read-only, local).

## Views

1. **Overview `/`** — stat tiles (satellites, operators, element sets, merge events), coverage
   meters, conflict counts linking into the explorer, ingest-ledger table, congestion heatmap
   teaser.
2. **Resolver `/resolver`** — search box; result list; identity card: header (name/NORAD/COSPAR/
   type/status badge), identifier crosswalk table grouped by source, ownership timeline
   (horizontal SCD2 bar segments per operator), status history, assertions table with
   conflicting attributes highlighted amber, latest orbit line, merge-audit footnote.
   Deep-linkable: `/resolver/:satelliteId`.
3. **Conflicts `/conflicts`** — three tabs (status / decay dates / stale owners), paginated
   tables, each row deep-links to the resolver. Header line cites the DQ report numbers.
4. **Operators `/operators`** — league table (sortable by fleet), detail panel on select:
   hierarchy (parents/children), fleet-by-status and by-regime mini-bars, acquisition history;
   full congestion heatmap below. Deep-linkable: `/operators/:operatorId`.

## Testing

- API: pytest (db-marked) with FastAPI TestClient against the live dev DB — shape assertions on
  every endpoint plus known-data spot checks (e.g. resolver returns ISS by name query).
- Web: `pnpm build` type-checks (strict TS) — that is the v1 bar; no component test harness yet.
- E2E: integration task curls every endpoint, builds the SPA, and verifies the served app loads.

## Out of scope (v1)

Auth, write operations, deployment, WebSockets/live refresh, exoplanet views, MCP server (the
API's SQL is written so the future MCP server can reuse it).
