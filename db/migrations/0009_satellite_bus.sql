-- Bus / manufacturer attribution for the Bus Benchmarks feature.
--
-- One row per satellite that GCAT attributes a bus model and/or a manufacturer org to.
-- Rebuilt from the latest OK raw_gcat_satcat snapshot by scripts/build_bus.py (full
-- delete + insert, idempotent). Resolution rules (bus-string normalization, org code ->
-- org name via raw_gcat_orgs, parent rollup) live in identity/bus.py and are documented
-- in docs/BUS_BENCHMARKS_METHODOLOGY.md. Field-level provenance follows the identity
-- layer's source_assertion pattern: the same build also extracts 'bus' and 'manufacturer'
-- assertions per GCAT row, so every resolved value here is traceable to a raw source row
-- (source, source_key=jcat, ingest_run_id).
CREATE TABLE satellite_bus (
    satellite_id            BIGINT PRIMARY KEY REFERENCES satellite,

    -- Bus model, from GCAT satcat's Bus column.
    bus_raw                 TEXT,               -- verbatim source string (may carry a trailing '?')
    bus_model               TEXT,               -- normalized display name (most common casing)
    bus_slug                TEXT,               -- stable lookup key: lower, [^a-z0-9]+ -> '-'
    bus_uncertain           BOOLEAN NOT NULL DEFAULT FALSE,  -- GCAT marked the value with '?'

    -- Manufacturer, from GCAT satcat's Manufacturer column (a GCAT org code).
    manufacturer_raw        TEXT,               -- verbatim source string
    manufacturer_code       TEXT,               -- primary (first-listed) GCAT org code
    manufacturer_codes      TEXT[],             -- all codes on co-manufactured objects
    manufacturer_uncertain  BOOLEAN NOT NULL DEFAULT FALSE,
    manufacturer_org_name   TEXT,               -- resolved leaf org display name
    manufacturer_group_code TEXT,               -- rolled-up org code (leaf when no rollup applies)
    manufacturer_name       TEXT,               -- rolled-up manufacturer display name
    manufacturer_slug       TEXT,               -- lower(manufacturer_group_code), slugified
    manufacturer_country    TEXT,               -- group org StateCode
    rollup_path             TEXT[],             -- leaf -> group org codes (rollup provenance)
    rollup_source           TEXT,               -- 'gcat_orgs' | 'gcat_orgs+override' | 'leaf' | 'unresolved'

    -- Row provenance.
    source                  TEXT NOT NULL DEFAULT 'gcat',
    source_key              TEXT NOT NULL,      -- GCAT jcat id of the attributing row
    ingest_run_id           BIGINT NOT NULL REFERENCES ingest_run,
    built_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (bus_model IS NOT NULL OR manufacturer_code IS NOT NULL)
);
CREATE INDEX ON satellite_bus (bus_slug);
CREATE INDEX ON satellite_bus (manufacturer_slug);

-- Immutable monthly captures of the benchmark leaderboards. scripts/build_bus.py inserts one
-- row per (month, kind, slug) with ON CONFLICT DO NOTHING, so the first refresh of a month
-- freezes that month's numbers and later refreshes never rewrite them. This longitudinal record
-- of the benchmark itself (metrics jsonb + the methodology version that produced them) is what
-- GET /api/buses/history/{slug} serves.
CREATE TABLE bus_benchmark_snapshots (
    snapshot_month      DATE NOT NULL,      -- first day of the captured month
    kind                TEXT NOT NULL,      -- 'manufacturer' | 'bus'
    slug                TEXT NOT NULL,
    display_name        TEXT NOT NULL,
    metrics             JSONB NOT NULL,     -- full leaderboard row at capture time
    methodology_version TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_month, kind, slug)
);
