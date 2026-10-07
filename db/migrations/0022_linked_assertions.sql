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
--    The resolver, the satellite page and the conflicts page apply this to their newest-claim
--    winners; the writer (identity/assertions.py) joins the same current links, so the two
--    agree from the first extraction after a retirement. Sources without a crosswalk key type
--    (a correction channel such as operator_confirmed) are always current.
--
--    Readers over the whole table call this AFTER picking their newest claim, on the ~140k
--    winners: joined first, the planner drives a nested loop from the crosswalk into the
--    35M-row table (measured 2026-10-06: a 300 s timeout against 19 s). The two orders agree,
--    because the writer only writes through current keys, so a stale key's rows are never
--    newer than the current key's.
CREATE OR REPLACE FUNCTION claim_is_current(sat BIGINT, src TEXT, key TEXT) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT CASE src WHEN 'satcat' THEN 'norad' WHEN 'gcat' THEN 'gcat_id' WHEN 'ucs' THEN 'ucs_row'
           END IS NULL
        OR EXISTS (
            SELECT 1 FROM satellite_identifier si
            WHERE si.satellite_id = sat AND si.source = src AND si.id_value = key
              AND si.id_type = CASE src WHEN 'satcat' THEN 'norad' WHEN 'gcat' THEN 'gcat_id'
                                        WHEN 'ucs' THEN 'ucs_row' END
              AND si.valid_to IS NULL)
$$;
