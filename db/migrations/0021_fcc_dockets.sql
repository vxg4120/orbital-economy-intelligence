-- Dockets: how FCC space-station filings relate over time, keyed on callsign.
--
-- This is deliberately NOT a supersession chain. Measured on the live corpus (2026-08-17),
-- SpaceX's S3069 docket carries four concurrent pending MODs, each modifying a different aspect
-- of the same authorization (Gen2 shells, V3, 2 GHz MSS, 1.5-1.6 GHz MSS); raw_ibfs_filings
-- carries no lead-filing key; and which earlier filing an amendment actually amends lives in its
-- prose, which nothing deterministic can read. So the model is the docket: every filing on a
-- callsign, dated, with its own status and its own specs. Nothing here asserts that one filing
-- supersedes another, and specs are never merged across filings.
--
-- Both views scope to the LATEST ok ingest run. raw_ibfs_filings accumulates every run's copy
-- (~815k rows for ~141k filings at spec time); an unscoped aggregate multiplies counts silently.
--
-- is_pending restates the predicate of v_fcc_pending_applications verbatim. The two are pinned
-- together by tests/test_filing_lineage.py with a both-directions EXCEPT, so neither can drift
-- without the suite catching it.

CREATE OR REPLACE VIEW v_fcc_docket_filing AS
WITH latest_filings AS (
    SELECT max(r.ingest_run_id) AS run_id
    FROM raw_ibfs_filings r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
    WHERE i.status = 'ok'
)
SELECT f.callsign,
       f.file_number,
       f.app_type_code,
       f.date_filed,
       f.status_code,
       f.date_grant,
       f.date_deny,
       f.date_dismiss,
       f.date_surrender,
       (f.date_filed IS NOT NULL AND f.date_grant IS NULL AND f.date_deny IS NULL
        AND f.date_dismiss IS NULL AND f.date_surrender IS NULL) AS is_pending
FROM raw_ibfs_filings f
JOIN latest_filings lf ON f.ingest_run_id = lf.run_id
WHERE f.subsystem_code = 'SAT'
  AND f.callsign IS NOT NULL AND f.callsign <> '';

CREATE OR REPLACE VIEW v_fcc_docket AS
SELECT callsign,
       count(*)                                                     AS filings_total,
       count(*) FILTER (WHERE is_pending)                           AS filings_pending,
       count(*) FILTER (WHERE date_grant IS NOT NULL)               AS filings_granted,
       count(*) FILTER (WHERE is_pending AND app_type_code = 'AMD') AS pending_amendments,
       min(date_filed)                                              AS first_filed,
       max(date_filed)                                              AS last_filed
FROM v_fcc_docket_filing
GROUP BY callsign;
