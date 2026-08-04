-- Filing document inventory: what is attached to each FCC space-station application.
--
-- The IBFS bulk dump carries the docket metadata but not the attachments; those live in the
-- ICFS portal (ServiceNow), whose anonymous page API lists each filing's documents with the
-- stable api-prod.fcc.gov download id. scripts/fetch_filing_documents.py harvests the
-- inventory for pending filings (bounded, paced, ledgered); rows persist across refetches
-- via the (file_number, sys_id) key, and vanished attachments are kept (a withdrawn exhibit
-- is itself a fact worth retaining).
--
-- Curated reading notes for high-value filings live in identity/fcc_filing_notes.yml and are
-- loaded into fcc_filing_note by the same fetch script: the analyst layer stays reviewable
-- in git, the serving layer stays in the database, and every note carries page citations
-- into the documents this table indexes.
CREATE TABLE IF NOT EXISTS fcc_filing_document (
    file_number   TEXT NOT NULL,
    sys_id        TEXT NOT NULL,
    doc_name      TEXT NOT NULL,
    doc_date      DATE,
    download_url  TEXT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (file_number, sys_id)
);
CREATE INDEX IF NOT EXISTS fcc_filing_document_file ON fcc_filing_document (file_number);

CREATE TABLE IF NOT EXISTS fcc_filing_note (
    file_number   TEXT PRIMARY KEY,
    summary       TEXT NOT NULL,
    key_points    TEXT[],
    source_doc    TEXT,
    source_pages  TEXT,
    noted_at      DATE NOT NULL,
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
