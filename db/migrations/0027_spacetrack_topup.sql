-- Space-Track nightly top-up (docs/specs/spacetrack-topup.md).
--
-- 1. spacetrack_cursor: the high-water mark of the nightly gp_history top-up. Space-Track's
--    usage policy allows gp_history "once per lifetime" for the same data ("once you download
--    an object's history ... do not download it again"), so each night asks only for element
--    sets CREATED after the last successful night's upper bound, never an overlapping window.
--    The cursor advances only when every batch of a night landed; a failed night leaves it put,
--    and the next night re-asks the same window (landing is ON CONFLICT DO NOTHING).
CREATE TABLE IF NOT EXISTS spacetrack_cursor (
    stream      TEXT PRIMARY KEY,           -- 'gp_history_topup'
    through     TIMESTAMPTZ NOT NULL,       -- CREATION_DATE upper bound of the last full pull
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. raw_spacetrack_decay: one row per Space-Track decay message per ingest run, landed
--    verbatim (payload keeps every field, typed columns are the ones readers use). The nightly
--    pulls the actual-decay messages (MSG_TYPE 'Historical') whose MSG_EPOCH falls in the last
--    30 days, once a day (Space-Track's DECAY class limit is 1/day). Each run is a full copy of
--    that window, so this is a snapshot table like every other raw_*: it is on
--    scripts/prune_snapshots.py's SNAPSHOT_TABLES and keeps the newest 3 OK runs plus each
--    month's first (docs/specs/raw-retention.md). Readers take the newest OK run.
CREATE TABLE IF NOT EXISTS raw_spacetrack_decay (
    norad_cat_id   BIGINT NOT NULL,
    object_name    TEXT,
    intldes        TEXT,
    country        TEXT,
    msg_epoch      TIMESTAMPTZ,
    decay_epoch    TEXT,                    -- verbatim; Space-Track sends a date or a datetime
    decay_date     DATE,                    -- decay_epoch's date, NULL when unparseable
    source         TEXT,                    -- Space-Track's message source (decay_msg, satcat, ...)
    msg_type       TEXT,                    -- 'Historical' = an actual decay; else a prediction
    precedence     INTEGER,                 -- lower = more recent data (Space-Track glossary)
    payload        JSONB NOT NULL,
    ingest_run_id  BIGINT NOT NULL REFERENCES ingest_run(ingest_run_id),
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_spacetrack_decay_run ON raw_spacetrack_decay (ingest_run_id);
CREATE INDEX IF NOT EXISTS raw_spacetrack_decay_norad ON raw_spacetrack_decay (norad_cat_id);

-- 3. The actual decay per object from the newest OK landing: one row per NORAD id, the
--    message with the lowest PRECEDENCE (Space-Track's "most recent data"), then the newest
--    MSG_EPOCH. satellite_id is the satellite the NORAD id currently identifies (NULL for an
--    object the graph has not matched). Not yet read by the resolver: wiring it in as the
--    decay_date source `spacetrack_decay` (already first in identity/precedence.yml) is the
--    spec's next step.
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
         si.satellite_id;
