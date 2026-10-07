-- Claims, not copies (docs/specs/assertion-model.md).
--
-- source_assertion holds one row per (source, key, attribute) per ingest run: every run
-- re-asserts a feed's full set, so by 2026-10-05 it was 35M rows and 4.7 GB for about 1.4M
-- distinct claims, and the copies cost memory and CPU as well as disk (the 90-minute nightly).
--
-- A claim is recorded once, when a feed first asserts (key, attribute, value), and closed when
-- the feed stops asserting it or asserts another value: closed_run is the first run in which it
-- was no longer made, NULL while it still is. A value that flips A -> B -> A is three rows.
--
-- Claims are about source keys, not satellites. Which satellite a claim is about is the
-- crosswalk's business (satellite_identifier, valid_to IS NULL), joined at read time through
-- v_current_claim, so retiring a link removes a sibling's claims from a satellite on the spot
-- (docs/specs/gcat-sibling-links.md), identity merges never touch claims, and an "unmatched
-- object" is an open claim whose key identifies no satellite.
CREATE TABLE claim (
    claim_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source         TEXT NOT NULL,
    source_key     TEXT NOT NULL,
    attribute      TEXT NOT NULL,
    value          TEXT NOT NULL,
    first_run      BIGINT NOT NULL REFERENCES ingest_run,
    observed_from  TIMESTAMPTZ NOT NULL,
    closed_run     BIGINT REFERENCES ingest_run,   -- NULL: still asserted
    observed_to    TIMESTAMPTZ,
    CONSTRAINT claim_closed_both CHECK ((closed_run IS NULL) = (observed_to IS NULL))
);
-- One open claim per (source, key, attribute): the writer's whole contract in one constraint.
CREATE UNIQUE INDEX claim_open_uq ON claim (source, source_key, attribute) WHERE closed_run IS NULL;
CREATE INDEX claim_key_idx ON claim (source, source_key, attribute);
CREATE INDEX claim_attr_idx ON claim (attribute) WHERE closed_run IS NULL;

-- The current claims per satellite: open claims through the keys that currently identify it.
CREATE OR REPLACE VIEW v_current_claim AS
SELECT si.satellite_id, c.claim_id, c.source, c.source_key, c.attribute, c.value,
       c.first_run, c.observed_from
FROM claim c
JOIN satellite_identifier si
  ON si.source = c.source AND si.id_value = c.source_key AND si.valid_to IS NULL
 AND si.id_type = CASE c.source WHEN 'satcat' THEN 'norad' WHEN 'gcat' THEN 'gcat_id'
                                WHEN 'ucs' THEN 'ucs_row' END
WHERE c.closed_run IS NULL;
