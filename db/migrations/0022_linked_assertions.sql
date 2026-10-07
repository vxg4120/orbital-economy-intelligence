-- One GCAT key, one satellite (docs/specs/gcat-sibling-links.md).
--
-- 1. identity_event learns 'identifier_revived': the matchers revive a retired link when a
--    catalog moves a key back, and no identity write is silent.
ALTER TABLE identity_event DROP CONSTRAINT IF EXISTS identity_event_event_ck;
ALTER TABLE identity_event ADD CONSTRAINT identity_event_event_ck CHECK (event IN (
    'key_churn_observed', 'identifier_expired', 'identifier_revived', 'provisional_promoted',
    'promotion_declined', 'occupancy_recorded'));

-- 2. A claim is current only while the key it came through still identifies the satellite.
--    source_assertion keeps every claim ever extracted (history is the product's premise), but
--    once a crosswalk link is retired, the claims that arrived through it must stop being
--    "what this source says about this satellite": a co-deployed sibling's owner, status, bus
--    or manufacturer, left over from a provisional identification GCAT has since revised.
--    The resolver, the satellite page and the conflicts page read through this view; the
--    writer (identity/assertions.py) joins the same current links, so the two agree from the
--    first extraction after a retirement.
--    v_current_source_key is the definition: the (satellite, source, source_key) triples that
--    currently identify a satellite. Readers over the whole table (identity/resolve.py,
--    api/routers/conflicts.py) pick their newest claim first and join this AFTER, on the
--    ~140k winners: joining it first makes the planner drive a nested loop from the crosswalk
--    into the 35M-row table (measured 2026-10-06: a 300 s timeout against 19 s). The two orders
--    agree, because the writer only writes through current keys, so a stale key's rows are
--    never newer than the current key's. v_linked_assertion is the joined form for reads of one
--    satellite (api/routers/satellites.py), where the index on satellite_id makes it cheap.
CREATE OR REPLACE VIEW v_current_source_key AS
SELECT satellite_id, source, id_value AS source_key
FROM satellite_identifier
WHERE valid_to IS NULL
  AND id_type = CASE source
                  WHEN 'satcat' THEN 'norad'
                  WHEN 'gcat' THEN 'gcat_id'
                  WHEN 'ucs' THEN 'ucs_row'
                END;

CREATE OR REPLACE VIEW v_linked_assertion AS
SELECT a.*
FROM source_assertion a
JOIN v_current_source_key k USING (satellite_id, source, source_key);
