-- Space-Track's actual decays as a claims feed (docs/specs/spacetrack-topup.md).
--
-- 1. spacetrack_decay_message: every Historical decay message any landing has carried. Each
--    nightly landing (raw_spacetrack_decay) holds only the messages whose MSG_EPOCH is in the
--    last 30 days, and the landings are pruned, so neither one landing nor the retained ones
--    are the whole record. identity/claims.py folds each OK landing in, oldest first: a message
--    it carries is (re)marked seen at that landing; a message it does not carry is withdrawn
--    only when the landing's window covers the message's epoch with a day to spare (an older
--    message has merely aged out, and a decay is permanent). The feed is the best surviving
--    message per object, recorded as an ordinary snapshot, so a withdrawal closes the claim.
CREATE TABLE IF NOT EXISTS spacetrack_decay_message (
    norad_cat_id   BIGINT NOT NULL,
    msg_epoch      TIMESTAMPTZ,
    decay_epoch    TEXT,
    decay_date     DATE,
    precedence     INTEGER,
    source         TEXT,
    first_run      BIGINT NOT NULL REFERENCES ingest_run(ingest_run_id),
    last_run       BIGINT NOT NULL REFERENCES ingest_run(ingest_run_id),
    withdrawn_run  BIGINT REFERENCES ingest_run(ingest_run_id),
    CONSTRAINT spacetrack_decay_message_uq UNIQUE NULLS NOT DISTINCT
        (norad_cat_id, msg_epoch, decay_epoch, precedence, source)
);

-- 2. One message per object everywhere it is chosen: the lowest PRECEDENCE (Space-Track's
--    "more recent data"), then the newest MSG_EPOCH, then the later date, so that a tie cannot
--    flip the choice from night to night (review, 2026-11-12). Same view, same columns, with the
--    tiebreak added. Known limits kept from 0027 (M-5): an OK landing with 0 rows is invisible
--    to `latest`, and a NORAD id with several current satcat links shows the lowest
--    satellite_id here, while v_claim gives the claim to every one of them.
CREATE OR REPLACE VIEW v_spacetrack_decay_actual AS
WITH latest AS (
    SELECT max(r.ingest_run_id) AS run_id
    FROM raw_spacetrack_decay r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
    WHERE i.status = 'ok'
)
SELECT DISTINCT ON (r.norad_cat_id)
    r.norad_cat_id,
    si.satellite_id,
    r.decay_date,
    r.decay_epoch,
    r.msg_epoch,
    r.source,
    r.precedence,
    r.ingest_run_id
FROM raw_spacetrack_decay r
JOIN latest ON r.ingest_run_id = latest.run_id
LEFT JOIN satellite_identifier si
  ON si.source = 'satcat' AND si.id_type = 'norad' AND si.valid_to IS NULL
 AND si.id_value = r.norad_cat_id::text
WHERE r.msg_type = 'Historical' AND r.decay_date IS NOT NULL
ORDER BY r.norad_cat_id, r.precedence ASC NULLS LAST, r.msg_epoch DESC NULLS LAST,
         r.decay_date DESC, si.satellite_id;

-- 3. v_claim's second branch: the 'spacetrack_decay' claims are keyed by NORAD id, and no
--    crosswalk link has that source, since Space-Track and SATCAT number objects the same way.
--    They reach a satellite through its current SATCAT NORAD link. The first branch, and its
--    plan, are unchanged (measured on production 2026-11-12 inside a rolled-back transaction:
--    the resolver's per-attribute reads and the satellite detail read are within noise).
--    v_current_claim selects from v_claim and picks the branch up unchanged.
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
