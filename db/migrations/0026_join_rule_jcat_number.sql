-- Bus methodology v1.10 (docs/BUS_BENCHMARKS_METHODOLOGY.md, docs/specs/gcat-sibling-links.md):
-- a GCAT row with no NORAD attributes to the satellite whose NORAD is its own key's number
-- (jcat S<n> is catalog number n in GCAT's scheme), before any COSPAR crosswalk lookup. The
-- join is published as its own rule so provenance shows which path fired; the CHECK below is
-- 0014's list with that value added. Drop-and-add is idempotent and runs in one transaction.

ALTER TABLE satellite_bus DROP CONSTRAINT IF EXISTS satellite_bus_join_rule_ck;
ALTER TABLE satellite_bus ADD CONSTRAINT satellite_bus_join_rule_ck CHECK (join_rule IN (
    'anchored_norad',        -- the raw catalog row carries the permanent anchor itself
    'anchored_jcat_number',  -- NORAD-less row; its key S<n> names the anchored satellite n
    'anchored_cospar',       -- anchored satellite reached via the COSPAR crosswalk
    'provisional_slot',      -- no permanent anchor: occupancy is a dated observation
    'operator_confirmed'     -- reserved for the correction channel overlay
));
