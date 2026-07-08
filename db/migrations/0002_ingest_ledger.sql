-- Ingestion ledger: politeness made visible
CREATE TABLE ingest_run (
    ingest_run_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source           TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    rows_ingested    INT,
    bytes_downloaded BIGINT,
    status           TEXT,            -- ok | skipped_fresh | error
    notes            TEXT
);
