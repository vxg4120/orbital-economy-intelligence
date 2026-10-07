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
CREATE OR REPLACE VIEW v_linked_assertion AS
SELECT a.*
FROM source_assertion a
JOIN satellite_identifier si
  ON si.satellite_id = a.satellite_id
 AND si.source = a.source
 AND si.id_value = a.source_key
 AND si.id_type = CASE a.source
                    WHEN 'satcat' THEN 'norad'
                    WHEN 'gcat' THEN 'gcat_id'
                    WHEN 'ucs' THEN 'ucs_row'
                  END
 AND si.valid_to IS NULL;
