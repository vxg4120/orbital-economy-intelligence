-- Landing table for GCAT's organizations file (orgs.tsv). Same pattern as the other GCAT raw
-- tables: a typed subset the identity layer needs + everything else preserved in `extra` JSONB,
-- parsed dynamically by header name (never by column position). orgs.tsv is the key that turns the
-- catalogs' owner CODES (GCAT `SPXS`, SATCAT `SPX`) into real operators — orgs.tsv Code is the join
-- key from raw_gcat_satcat.owner, and orgs.tsv Name/EName/StateCode/Class/Type/Parent supply the
-- operator identity, country, class and hierarchy.
--
-- Header reference: orgs.tsv starts `#Code Ucode StateCode Type Class TStart TStop ... Name EName
-- ... Parent`. Columns outside the typed subset land verbatim in `extra`, so source drift never
-- drops information.

CREATE TABLE raw_gcat_orgs (
    code          TEXT NOT NULL,
    ucode         TEXT,
    state_code    TEXT,
    org_type      TEXT,
    org_class     TEXT,
    t_start       TEXT,
    t_stop        TEXT,
    short_name    TEXT,
    name          TEXT,
    e_name        TEXT,
    parent_code   TEXT,
    extra         JSONB NOT NULL DEFAULT '{}',
    ingest_run_id BIGINT NOT NULL REFERENCES ingest_run,
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, ingest_run_id)
);
