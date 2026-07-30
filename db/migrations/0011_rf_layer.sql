-- RF authorization layer v1 (docs/design/0003-rf-authorization-layer.md): who transmits what.
-- Two raw sources land here append-only with the standard ingest_run pattern (latest OK run
-- wins, history retained), and one built table resolves FCC authorizations onto canonical
-- satellites with the match tier recorded per row.

-- SatNOGS DB transmitters (https://db.satnogs.org, CC BY-SA 4.0). Kept as a separable,
-- attributed source: rows carry SatNOGS's own citation field, and anything derived from this
-- table inherits the share-alike license, which is why it never silently merges into other
-- tables. norad_cat_id can be NULL for objects SatNOGS has not identified yet.
CREATE TABLE IF NOT EXISTS raw_satnogs_transmitters (
    uuid            TEXT NOT NULL,
    norad_cat_id    BIGINT,
    sat_id          TEXT,
    description     TEXT,
    type            TEXT,
    status          TEXT,
    alive           BOOLEAN,
    downlink_low    BIGINT,
    downlink_high   BIGINT,
    uplink_low      BIGINT,
    uplink_high     BIGINT,
    mode            TEXT,
    baud            DOUBLE PRECISION,
    service         TEXT,
    citation        TEXT,
    iaru_coordination TEXT,
    frequency_violation BOOLEAN,
    unconfirmed     BOOLEAN,
    updated         TIMESTAMPTZ,
    ingest_run_id   BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_satnogs_transmitters_norad
    ON raw_satnogs_transmitters (norad_cat_id);
CREATE INDEX IF NOT EXISTS raw_satnogs_transmitters_run
    ON raw_satnogs_transmitters (ingest_run_id);

-- FCC Approved Space Station List (ssal.xlsx): every current Part 25 license and market-access
-- grant, one row per (satellite or NGSO system) x frequency-band entry as published.
CREATE TABLE IF NOT EXISTS raw_fcc_ssal (
    orbital_location    TEXT,
    satellite_name      TEXT,
    call_sign           TEXT,
    licensee            TEXT,
    administration      TEXT,
    service             TEXT,
    frequency_range     TEXT,
    in_orbit_date       TEXT,
    grant_type          TEXT,
    notes               TEXT,
    ingest_run_id       BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_fcc_ssal_run ON raw_fcc_ssal (ingest_run_id);

-- Built by scripts/build_rf.py: FCC authorizations resolved onto canonical satellites. The
-- match_tier records HOW each row was joined, because the three tiers carry very different
-- confidence: 'constellation' rides a curated blanket-license mapping, 'gso_name' is an exact
-- normalized-name match against the grant list, and unresolved grants simply do not appear
-- here (absence over guesses, same rule as the rest of the identity layer).
CREATE TABLE IF NOT EXISTS satellite_fcc_authorization (
    satellite_id    BIGINT NOT NULL REFERENCES satellite (satellite_id) ON DELETE CASCADE,
    call_sign       TEXT NOT NULL,
    satellite_name  TEXT,
    licensee        TEXT,
    service         TEXT,
    frequency_range TEXT NOT NULL,
    grant_type      TEXT,
    match_tier      TEXT NOT NULL CHECK (match_tier IN ('constellation', 'gso_name')),
    match_detail    TEXT,
    ingest_run_id   BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    built_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (satellite_id, call_sign, frequency_range)
);
CREATE INDEX IF NOT EXISTS satellite_fcc_authorization_call
    ON satellite_fcc_authorization (call_sign);
