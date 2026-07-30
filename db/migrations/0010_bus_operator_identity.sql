-- Phase 4 (docs/design/0002-phase4-brief.md): operator-identity merge layer for manufacturer
-- attribution, and the URL contract that protects published slugs when cohorts merge.

-- Which operator the leaf manufacturer code and the rolled-up group code resolve to, under the
-- restricted alias1 rule (sources gcat_orgs/gcat/seed only, deterministic tiebreak). Nullable:
-- a NULL group operator means the code forms its own cohort exactly as it did before this
-- migration, which is the fallback for the ~317 group codes the operator graph does not know.
ALTER TABLE satellite_bus ADD COLUMN IF NOT EXISTS manufacturer_operator_id BIGINT;
ALTER TABLE satellite_bus ADD COLUMN IF NOT EXISTS manufacturer_group_operator_id BIGINT;

-- A slug is a public URL (/buses/{slug}) and the primary key of the frozen monthly archive
-- (bus_benchmark_snapshots). When an attribution rule retires a slug by merging its cohort into
-- another, the old URL must keep resolving and the frozen series must stay reachable. One row per
-- retired slug; strictly one target (a split has no legal alias, by design).
CREATE TABLE IF NOT EXISTS benchmark_slug_alias (
    kind        TEXT NOT NULL CHECK (kind IN ('manufacturer', 'bus')),
    old_slug    TEXT NOT NULL,
    new_slug    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, old_slug),
    CHECK (old_slug <> new_slug)
);
