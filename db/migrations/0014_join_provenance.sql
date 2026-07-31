-- Tenancy Phase 3 (docs/design/0001-tenancy.md section 4.4): join provenance on attribution.
-- Every satellite_bus row records HOW it was joined to its canonical satellite, because the
-- three paths carry categorically different confidence: a permanent anchor cannot move under
-- the row, a COSPAR crosswalk to an anchored satellite is solid but indirect, and a provisional
-- slot is a dated observation of occupancy, not an identity. No invented confidence floats:
-- a categorical rule plus a boolean churn observation, both auditable.

ALTER TABLE satellite_bus
    ADD COLUMN IF NOT EXISTS join_rule TEXT NOT NULL DEFAULT 'anchored_norad',
    ADD COLUMN IF NOT EXISTS key_churn_observed BOOLEAN NOT NULL DEFAULT FALSE;

DO $$ BEGIN
    ALTER TABLE satellite_bus ADD CONSTRAINT satellite_bus_join_rule_ck CHECK (join_rule IN (
        'anchored_norad',      -- the raw catalog row carries the permanent anchor itself
        'anchored_cospar',     -- anchored satellite reached via the COSPAR crosswalk
        'provisional_slot',    -- no permanent anchor: occupancy is a dated observation
        'operator_confirmed'   -- reserved for the correction channel overlay
    ));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS satellite_bus_join_rule_idx ON satellite_bus (join_rule);
