-- Space-Track's actual decays as a claims feed (docs/specs/spacetrack-topup.md).
--
-- identity/claims.py records the newest raw_spacetrack_decay landing as source
-- 'spacetrack_decay', keyed by NORAD id, one claim per object. No crosswalk link has that
-- source: Space-Track and SATCAT number objects the same way, so these claims reach a
-- satellite through its current SATCAT NORAD link. That is a second branch of v_claim rather
-- than a change to the first, so every existing source keeps its join and its plan; the
-- branch reads only the 'spacetrack_decay' claims (a few hundred rows a month).
-- v_current_claim selects from v_claim and picks the branch up unchanged.
CREATE OR REPLACE VIEW v_claim AS
SELECT si.satellite_id, c.claim_id, c.source, c.source_key, c.attribute, c.value,
       c.first_run, c.observed_from, c.closed_run, coalesce(c.observed_to, p.observed_at) AS observed_to
FROM claim c
JOIN claim_progress p ON p.source = c.source
JOIN satellite_identifier si
  ON si.source = c.source AND si.id_value = c.source_key AND si.valid_to IS NULL
 AND si.id_type = CASE c.source WHEN 'satcat' THEN 'norad' WHEN 'gcat' THEN 'gcat_id'
                                WHEN 'ucs' THEN 'ucs_row' END
UNION ALL
SELECT si.satellite_id, c.claim_id, c.source, c.source_key, c.attribute, c.value,
       c.first_run, c.observed_from, c.closed_run, coalesce(c.observed_to, p.observed_at) AS observed_to
FROM claim c
JOIN claim_progress p ON p.source = c.source
JOIN satellite_identifier si
  ON si.source = 'satcat' AND si.id_type = 'norad' AND si.id_value = c.source_key
 AND si.valid_to IS NULL
WHERE c.source = 'spacetrack_decay';
