-- Identity graph: SPEC.md §5, verbatim, with two documented deviations to source_assertion:
--   1. source_key TEXT NOT NULL added after satellite_id (source-native object key, so unmatched
--      assertions are not orphans that can never be attached after matching).
--   2. ingest_run_id is a real FK to ingest_run (not a bare BIGINT).
-- Plus an additional index on source_assertion (source, source_key).

-- Canonical physical object
CREATE TABLE satellite (
    satellite_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    norad_id         BIGINT UNIQUE,            -- BIGINT: 9-digit era. NULL until cataloged
    cospar_id        TEXT,                     -- e.g. '2023-054A'
    canonical_name   TEXT NOT NULL,
    object_type      TEXT NOT NULL DEFAULT 'UNKNOWN',  -- PAYLOAD | ROCKET_BODY | DEBRIS | UNKNOWN
    launch_date      DATE,
    decay_date       DATE,                     -- resolved value; conflicts live in assertions
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Identifier crosswalk: the heart of the graph
CREATE TABLE satellite_identifier (
    identifier_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    satellite_id     BIGINT NOT NULL REFERENCES satellite,
    id_type          TEXT NOT NULL,   -- norad | cospar | name_satcat | name_operator |
                                      -- name_gcat | gcat_id | ucs_row | itu_filing | fcc_callsign
    id_value         TEXT NOT NULL,
    valid_from       DATE,
    valid_to         DATE,            -- NULL = current
    source           TEXT NOT NULL,
    confidence       NUMERIC(3,2) NOT NULL DEFAULT 1.00,
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id_type, id_value, source, satellite_id)
);
CREATE INDEX ON satellite_identifier (id_value);

-- Operators and their hierarchy (the MSO tree)
CREATE TABLE operator (
    operator_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name   TEXT NOT NULL UNIQUE,
    country          TEXT,
    operator_class   TEXT              -- commercial | civil | defense | academic | mixed
);

CREATE TABLE operator_alias (
    operator_id      BIGINT NOT NULL REFERENCES operator,
    alias            TEXT NOT NULL,
    source           TEXT NOT NULL,
    PRIMARY KEY (operator_id, alias, source)
);

CREATE TABLE operator_relationship (
    child_id         BIGINT NOT NULL REFERENCES operator,
    parent_id        BIGINT NOT NULL REFERENCES operator,
    relationship     TEXT NOT NULL,    -- subsidiary_of | brand_of | acquired_by | merged_into
    valid_from       DATE,
    valid_to         DATE,
    source           TEXT NOT NULL,
    PRIMARY KEY (child_id, parent_id, relationship, valid_from)
);

-- Temporal ownership: SCD Type 2, the OneWeb->Eutelsat problem
CREATE TABLE satellite_operator (
    satellite_id     BIGINT NOT NULL REFERENCES satellite,
    operator_id      BIGINT NOT NULL REFERENCES operator,
    role             TEXT NOT NULL,    -- owner | operator | manufacturer
    valid_from       DATE NOT NULL,
    valid_to         DATE,             -- NULL = current
    source           TEXT NOT NULL,
    confidence       NUMERIC(3,2) NOT NULL DEFAULT 1.00,
    PRIMARY KEY (satellite_id, operator_id, role, valid_from)
);

-- Canonical status taxonomy + per-source mappings
-- Canonical set: ACTIVE | PARTIAL | SPARE | INACTIVE | GRAVEYARD | DECAYED | UNKNOWN
CREATE TABLE status_mapping (
    source           TEXT NOT NULL,    -- satcat | gcat | ucs | supgp
    source_value     TEXT NOT NULL,    -- e.g. '+', 'P', 'B', GCAT phase codes
    canonical_status TEXT NOT NULL,
    notes            TEXT,
    PRIMARY KEY (source, source_value)
);
-- Populate from each source's current documentation during build;
-- do not trust third-party blog tables for the code meanings.

CREATE TABLE satellite_status_history (
    satellite_id     BIGINT NOT NULL REFERENCES satellite,
    canonical_status TEXT NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    source           TEXT NOT NULL,
    PRIMARY KEY (satellite_id, observed_at, source)
);

-- Field-level provenance: what each source claims, before resolution
CREATE TABLE source_assertion (
    assertion_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    satellite_id     BIGINT REFERENCES satellite,   -- NULL until matched
    source_key       TEXT NOT NULL,    -- source-native object key (satcat: norad text; gcat: jcat;
                                        -- ucs: row hash) so unmatched assertions stay attachable
    attribute        TEXT NOT NULL,   -- owner | status | decay_date | object_type | name
    value            TEXT NOT NULL,
    source           TEXT NOT NULL,
    observed_at      TIMESTAMPTZ NOT NULL,
    ingest_run_id    BIGINT NOT NULL REFERENCES ingest_run
);
CREATE INDEX ON source_assertion (satellite_id, attribute);
CREATE INDEX ON source_assertion (source, source_key);

-- Merge audit: no silent merges, ever
CREATE TABLE merge_log (
    merge_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    surviving_id     BIGINT NOT NULL,
    merged_id        BIGINT NOT NULL,
    rule_fired       TEXT NOT NULL,   -- e.g. 'norad_exact', 'cospar+name_fuzzy>=0.92'
    score            NUMERIC(4,3),
    merged_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    details          JSONB
);
