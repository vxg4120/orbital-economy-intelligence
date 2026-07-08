-- Per-source landing tables. All raw tables carry ingest_run_id + loaded_at for lineage.

CREATE TABLE raw_satcat (
    object_name      TEXT,
    object_id        TEXT,
    norad_cat_id     BIGINT NOT NULL,
    object_type      TEXT,
    ops_status_code  TEXT,
    owner            TEXT,
    launch_date      DATE,
    launch_site      TEXT,
    decay_date       DATE,
    period           NUMERIC,
    inclination      NUMERIC,
    apogee           NUMERIC,
    perigee          NUMERIC,
    rcs              NUMERIC,
    data_status_code TEXT,
    orbit_center     TEXT,
    orbit_type       TEXT,
    ingest_run_id    BIGINT NOT NULL REFERENCES ingest_run,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (norad_cat_id, ingest_run_id)
);

CREATE TABLE raw_gcat_satcat (
    jcat             TEXT NOT NULL,
    norad_id         BIGINT,
    piece            TEXT,
    object_type      TEXT,
    name             TEXT,
    pl_name          TEXT,
    launch_date      TEXT,
    decay_date       TEXT,
    status           TEXT,
    dest             TEXT,
    owner            TEXT,
    state            TEXT,
    manufacturer     TEXT,
    bus              TEXT,
    mass             TEXT,
    perigee_km       NUMERIC NULL,
    apogee_km        NUMERIC NULL,
    inc_deg          NUMERIC NULL,
    op_orbit         TEXT,
    alt_names        TEXT,
    extra            JSONB NOT NULL DEFAULT '{}',
    ingest_run_id    BIGINT NOT NULL REFERENCES ingest_run,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (jcat, ingest_run_id)
);

CREATE TABLE raw_gcat_psatcat (
    jcat             TEXT NOT NULL,
    piece            TEXT,
    name             TEXT,
    extra            JSONB NOT NULL DEFAULT '{}',
    ingest_run_id    BIGINT NOT NULL REFERENCES ingest_run,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (jcat, ingest_run_id)
);

CREATE TABLE raw_ucs (
    row_key          TEXT NOT NULL,
    name             TEXT,
    country_operator TEXT,
    operator         TEXT,
    users            TEXT,
    purpose          TEXT,
    norad_id         BIGINT NULL,
    cospar_id        TEXT NULL,
    launch_date      TEXT,
    extra            JSONB NOT NULL DEFAULT '{}',
    ingest_run_id    BIGINT NOT NULL REFERENCES ingest_run,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (row_key, ingest_run_id)
);

CREATE TABLE raw_supgp_status (
    raw_supgp_status_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    norad_id         BIGINT NULL,
    object_name      TEXT,
    file_tag         TEXT,
    flag             TEXT,
    detail           TEXT,
    ingest_run_id    BIGINT NOT NULL REFERENCES ingest_run,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
