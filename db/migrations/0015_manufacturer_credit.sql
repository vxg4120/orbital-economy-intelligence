-- Co-builder participation bridge (Phase 4 brief section 6, methodology v1.6).
--
-- One row per (satellite, credited manufacturer cohort), expanded from the '/'-separated
-- GCAT manufacturer string with WITH ORDINALITY and resolved through the SAME rollup walk
-- and operator merge that produce the headline manufacturer_slug. position records where in
-- the string the credit came from (1 = prime, the headline attribution); arity records how
-- many builders the string named. The primary key absorbs self-collapsing compound builds
-- (both codes rolling up to one group keep a single row at the lowest position).
--
-- fleet_total stays position-1 only everywhere. participated_total is derived from this
-- table on the detail payload ONLY: never on the leaderboard, never in the frozen snapshot
-- metrics, never as a sort key.
CREATE TABLE IF NOT EXISTS satellite_manufacturer_credit (
    satellite_id      BIGINT   NOT NULL REFERENCES satellite(satellite_id),
    manufacturer_slug TEXT     NOT NULL,
    position          SMALLINT NOT NULL CHECK (position >= 1),
    arity             SMALLINT NOT NULL CHECK (arity >= position),
    uncertain         BOOLEAN  NOT NULL DEFAULT FALSE,
    built_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (satellite_id, manufacturer_slug)
);

CREATE INDEX IF NOT EXISTS satellite_manufacturer_credit_slug_idx
    ON satellite_manufacturer_credit (manufacturer_slug);
