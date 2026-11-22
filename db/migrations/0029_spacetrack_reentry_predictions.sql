-- Space-Track 60-day reentry predictions (docs/specs/spacetrack-reentry-predictions.md).
--
-- 1. raw_spacetrack_decay_prediction: one row per Space-Track 60-day decay message per ingest
--    run (ingest_run endpoint 'decay_60day'), landed verbatim with the same columns as
--    raw_spacetrack_decay. Space-Track publishes this set once a week, on Wednesdays after 17:00
--    UTC, and asks for one query a week. It is kept apart from raw_spacetrack_decay on purpose:
--    every reader of that table takes its newest OK landing as the whole set of actual decays,
--    so a predictions-only landing there would read as a night without any decay, and the claims
--    pass (identity/claims.py) would close every Space-Track decay claim. A prediction is never
--    a decay: nothing in identity reads this table. Each run is a full copy of the set, so it
--    is a snapshot table on scripts/prune_snapshots.py's SNAPSHOT_TABLES.
CREATE TABLE IF NOT EXISTS raw_spacetrack_decay_prediction (
    norad_cat_id   BIGINT NOT NULL,
    object_name    TEXT,
    intldes        TEXT,
    country        TEXT,
    msg_epoch      TIMESTAMPTZ,             -- when Space-Track issued the prediction
    decay_epoch    TEXT,                    -- verbatim predicted decay epoch
    decay_date     DATE,                    -- decay_epoch's date, NULL when unparseable
    source         TEXT,                    -- '60day_msg'
    msg_type       TEXT,
    precedence     INTEGER,                 -- lower = more recent data (Space-Track glossary)
    payload        JSONB NOT NULL,
    ingest_run_id  BIGINT NOT NULL REFERENCES ingest_run(ingest_run_id),
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_spacetrack_decay_prediction_run
    ON raw_spacetrack_decay_prediction (ingest_run_id);
CREATE INDEX IF NOT EXISTS raw_spacetrack_decay_prediction_norad
    ON raw_spacetrack_decay_prediction (norad_cat_id);

-- 2. The current prediction per object: the newest OK 'decay_60day' run is the whole set, one
--    row per NORAD id. A pull asks for eight days of messages, so it can hold last week's
--    bulletin beside this week's: the newest MSG_EPOCH wins, then the lowest PRECEDENCE (the
--    glossary says lower is more recent data), then the earliest predicted date and the
--    verbatim epoch, so the pick is deterministic and an undated message loses to a dated one
--    of the same issue. The newest run is taken from the ledger, not from the rows, so an OK
--    run that landed nothing empties the view instead of leaving last week's predictions on
--    show (the limit recorded for v_spacetrack_decay_actual in migration 0027). satellite_id
--    is the satellite whose canonical NORAD number this is (satellite.norad_id is unique), so
--    an object GCAT catalogued before SATCAT still finds its satellite; NULL when the graph has
--    no such satellite.
CREATE OR REPLACE VIEW v_reentry_prediction_latest AS
WITH latest AS (
    SELECT max(ingest_run_id) AS run_id
    FROM ingest_run
    WHERE source = 'spacetrack' AND endpoint = 'decay_60day' AND status = 'ok'
)
SELECT DISTINCT ON (r.norad_cat_id)
    r.norad_cat_id,
    s.satellite_id,
    r.object_name,
    r.decay_date AS predicted_date,
    r.decay_epoch,
    r.msg_epoch,
    r.precedence,
    r.ingest_run_id
FROM raw_spacetrack_decay_prediction r
JOIN latest ON r.ingest_run_id = latest.run_id
LEFT JOIN satellite s ON s.norad_id = r.norad_cat_id
ORDER BY r.norad_cat_id, r.msg_epoch DESC NULLS LAST, r.precedence ASC NULLS LAST,
         r.decay_date ASC NULLS LAST, r.decay_epoch;
