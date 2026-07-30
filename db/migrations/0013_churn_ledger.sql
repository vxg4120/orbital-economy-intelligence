-- Tenancy Phase 2 (docs/design/0001-tenancy.md sections 3.1, 4.2, 4.3): the churn ledger.
-- Catalogs reassign provisional keys and re-identify slot occupants on fresh launches; this
-- migration gives the identity layer the vocabulary to OBSERVE that instead of being bitten by
-- it. Everything here is additive.

-- Shared key derivation, defined once in SQL so set-based joins can use it.
-- NOTE: substring(text FROM pattern) returns only the FIRST capture group when the pattern has
-- capture groups, which silently reduces a launch key to a calendar year. regexp_match returns
-- the whole array, so that is what we use, zero-padding the launch number:
-- '2026-156BH' -> '2026-156', '2026-56A' -> '2026-056'.
CREATE OR REPLACE FUNCTION oei_launch_key(piece text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $f$
SELECT CASE
  WHEN m IS NULL THEN NULL
  ELSE m[1] || '-' || lpad(m[2], 3, '0')
END
FROM (SELECT regexp_match(btrim(COALESCE($1, '')), '^([0-9]{4})[- ]?([0-9]{1,3})') AS m) t
$f$;

-- SQL twin of identity.normalize.norm_name. tests/test_identity_keys.py re-runs a full-corpus
-- equivalence check on every pass so the two definitions cannot drift apart.
CREATE OR REPLACE FUNCTION oei_name_key(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $f$
SELECT NULLIF(regexp_replace(
  btrim(regexp_replace(regexp_replace(regexp_replace(
    regexp_replace(lower(COALESCE($1, '')), '[\(\[\{][^\)\]\}]*[\)\]\}]', ' ', 'g'),
    '[^[:alnum:][:space:]_]+', ' ', 'g'),
    '([[:alpha:]])([[:digit:]])', '\1 \2', 'g'),
    '([[:digit:]])([[:alpha:]])', '\1 \2', 'g')),
  '[[:space:]]+', ' ', 'g'), '')
$f$;

-- Measured key stability, scoped per (source, id_type). Not a hand-tuned ladder: every column
-- is an observation with a denominator, rebuilt set-based from the retained snapshots.
CREATE TABLE IF NOT EXISTS key_stability (
    source              TEXT   NOT NULL,
    id_type             TEXT   NOT NULL,
    observations        BIGINT NOT NULL,
    referent_changes    BIGINT NOT NULL,
    changes_anchored    BIGINT NOT NULL,
    prev_run_id         BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    curr_run_id         BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    measured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, id_type, prev_run_id, curr_run_id)
);

-- The churn ledger. One row per observed reassignment. Absence is never the signal (catalog
-- ids do not vanish between snapshots); referent change is.
CREATE TABLE IF NOT EXISTS catalog_key_churn (
    source        TEXT   NOT NULL,
    id_type       TEXT   NOT NULL,
    id_value      TEXT   NOT NULL,
    prev_run_id   BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    curr_run_id   BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    prev_name     TEXT,
    curr_name     TEXT,
    prev_name_key TEXT,
    curr_name_key TEXT,
    prev_owner    TEXT,
    curr_owner    TEXT,
    prev_anchored BOOLEAN NOT NULL,
    launch_key    TEXT,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, id_type, id_value, prev_run_id, curr_run_id)
);
CREATE INDEX IF NOT EXISTS catalog_key_churn_key_idx ON catalog_key_churn (id_type, id_value);
CREATE INDEX IF NOT EXISTS catalog_key_churn_launch_idx ON catalog_key_churn (launch_key);

-- No silent identity write, ever. Generalizes merge_log's contract to expiry, promotion and
-- churn observation; merge_log stays exactly as it is.
CREATE TABLE IF NOT EXISTS identity_event (
    identity_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    satellite_id      BIGINT,               -- deliberately no FK: must survive a merge delete
    event             TEXT   NOT NULL,
    rule_fired        TEXT   NOT NULL,
    ingest_run_id     BIGINT REFERENCES ingest_run (ingest_run_id),
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    details           JSONB,
    CONSTRAINT identity_event_event_ck CHECK (event IN (
        'key_churn_observed', 'identifier_expired', 'provisional_promoted',
        'promotion_declined', 'occupancy_recorded'))
);
CREATE INDEX IF NOT EXISTS identity_event_sat_idx ON identity_event (satellite_id, at DESC);
CREATE INDEX IF NOT EXISTS identity_event_event_idx ON identity_event (event, at DESC);

-- Make satellite_identifier.valid_to load-bearing: the column has existed since 0004 and
-- nothing ever wrote it.
CREATE INDEX IF NOT EXISTS satellite_identifier_current_idx
    ON satellite_identifier (id_type, id_value, source)
    WHERE valid_to IS NULL;

-- Anchor state on the satellite itself: provisional exactly while it carries no permanent
-- identifier from a minting authority. Backfilled here; maintained by the pipeline thereafter.
ALTER TABLE satellite ADD COLUMN IF NOT EXISTS anchor_state TEXT NOT NULL DEFAULT 'provisional';
ALTER TABLE satellite ADD COLUMN IF NOT EXISTS anchor_source TEXT;
DO $$ BEGIN
    ALTER TABLE satellite ADD CONSTRAINT satellite_anchor_state_ck
        CHECK (anchor_state IN ('anchored', 'provisional'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

UPDATE satellite SET anchor_state = 'anchored', anchor_source = 'satcat'
WHERE norad_id IS NOT NULL AND anchor_state <> 'anchored';

CREATE INDEX IF NOT EXISTS satellite_provisional_idx ON satellite (anchor_state)
    WHERE anchor_state = 'provisional';
