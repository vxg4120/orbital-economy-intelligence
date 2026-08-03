-- Applicant identity for IBFS filings + the curated applicant->cohort link (methodology v1.7).
--
-- main.dat column 41 is the filing's applicant ADDRESS key into address.dat, whose rows carry
-- the organization name and FRN (FCC Registration Number, the Commission's stable org-level
-- identity). Landing both gives every pending application an applicant identity, which is what
-- the Bus Benchmarks forward signal joins on: a pending application lights up a manufacturer
-- page only when the applicant's FRN is curated to that cohort (identity/fcc_applicants.yml),
-- i.e. when the same corporate group both builds satellites and files with the FCC. Applicant
-- is NOT builder in general; incumbent operators who buy their spacecraft match no cohort.

ALTER TABLE raw_ibfs_filings ADD COLUMN IF NOT EXISTS applicant_address_key BIGINT;
ALTER TABLE raw_ibfs_filings ADD COLUMN IF NOT EXISTS frn TEXT;

CREATE TABLE IF NOT EXISTS raw_ibfs_addresses (
    address_key   BIGINT,
    name          TEXT,
    city          TEXT,
    state_code    TEXT,
    country       TEXT,
    frn           TEXT,
    ingest_run_id BIGINT NOT NULL REFERENCES ingest_run(ingest_run_id),
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_ibfs_addresses_key ON raw_ibfs_addresses (address_key);
CREATE INDEX IF NOT EXISTS raw_ibfs_addresses_run ON raw_ibfs_addresses (ingest_run_id);
CREATE INDEX IF NOT EXISTS raw_ibfs_addresses_frn ON raw_ibfs_addresses (frn);

-- Curated FRN -> manufacturer cohort links, rebuilt by scripts/build_rf.py from
-- identity/fcc_applicants.yml (slugs validated against the live leaderboard, merge-retired
-- slugs resolved through benchmark_slug_alias to the surviving cohort).
CREATE TABLE IF NOT EXISTS fcc_applicant_link (
    frn               TEXT PRIMARY KEY,
    manufacturer_slug TEXT NOT NULL,
    applicant_note    TEXT,
    built_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fcc_applicant_link_slug ON fcc_applicant_link (manufacturer_slug);

-- Append applicant identity to the pending-applications view (CREATE OR REPLACE VIEW can only
-- APPEND columns, so the two new ones go last).
CREATE OR REPLACE VIEW v_fcc_pending_applications AS
WITH latest_filings AS (
    SELECT max(r.ingest_run_id) AS run_id FROM raw_ibfs_filings r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'
),
latest_stations AS (
    SELECT max(r.ingest_run_id) AS run_id FROM raw_ibfs_space_stations r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'
),
latest_addresses AS (
    SELECT max(r.ingest_run_id) AS run_id FROM raw_ibfs_addresses r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'
),
stations AS (
    SELECT
        COALESCE(substring(upper(s.us_name) FROM '\(([A-Z]+[0-9][0-9A-Z-]*)\)'),
                 upper(s.us_name)) AS join_key,
        string_agg(DISTINCT s.us_name, ' / ') AS us_names,
        string_agg(DISTINCT s.itu_name, ' / ') AS itu_names,
        string_agg(DISTINCT s.orbit_location, ' / ') AS orbit_locations
    FROM (
        SELECT DISTINCT r.us_name, r.itu_name, r.orbit_location
        FROM raw_ibfs_space_stations r, latest_stations ls
        WHERE r.ingest_run_id = ls.run_id AND r.us_name IS NOT NULL
    ) s
    GROUP BY 1
),
addresses AS (
    SELECT DISTINCT ON (r.address_key) r.address_key, r.name, r.frn
    FROM raw_ibfs_addresses r, latest_addresses la
    WHERE r.ingest_run_id = la.run_id AND r.address_key IS NOT NULL
    ORDER BY r.address_key, r.name
)
SELECT
    f.filing_key,
    f.file_number,
    f.callsign,
    f.app_type_code,
    f.status_code,
    f.status_date,
    f.last_action,
    f.date_filed,
    f.date_last_update,
    s.us_names        AS satellite_name,
    s.itu_names       AS itu_name,
    s.orbit_locations AS orbit_location,
    f.description,
    a.name                     AS applicant_name,
    COALESCE(a.frn, f.frn)     AS applicant_frn
FROM raw_ibfs_filings f
JOIN latest_filings lf ON f.ingest_run_id = lf.run_id
LEFT JOIN stations s ON s.join_key = upper(f.callsign)
LEFT JOIN addresses a ON a.address_key = f.applicant_address_key
WHERE f.subsystem_code = 'SAT'
  AND f.date_filed IS NOT NULL
  AND f.date_grant IS NULL
  AND f.date_deny IS NULL
  AND f.date_dismiss IS NULL
  AND f.date_surrender IS NULL
ORDER BY f.date_filed DESC;
