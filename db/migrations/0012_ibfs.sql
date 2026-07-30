-- FCC IBFS legacy relational dump (ftp://ftp.fcc.gov/pub/Bureaus/International/databases/
-- IBFS.zip, US Government work, refreshed roughly weekly). This is the layer the Approved
-- Space Station List cannot give us: the full Part 25 filing HISTORY including pending
-- applications (the pre-launch pipeline), the SPACE_STATION reference table, and structured
-- per-authorization frequency rows. Only three of the dump's ~70 tables land here; the DDL for
-- all of them is ibfs.txt in the same FTP directory (dated 1998; the live dump carries extra
-- undocumented trailing columns which the loader ignores).
--
-- Format facts verified against the live dump 2026-07-30, encoded in ingest/ibfs.py:
--   * records terminate with '|^|' + CRLF; some MAIN text fields contain embedded CRLFs
--   * file numbers are stored WITHOUT dashes ('SATLOA2025061800152', not the
--     'SAT-LOA-20250618-00152' form the IBFS web UI displays)
--   * the space station table is exported massively duplicated (~144k rows, ~837 distinct)
-- All tables land append-only with the standard ingest_run pattern (latest OK run wins,
-- history retained). Dates are Sybase datetimes ('Jun 18 2025  4:50:24:023PM'); the loader
-- keeps the calendar date, which is the analytical payload, and the saved raw zip retains
-- full fidelity.

-- IBFS MAIN, the filing docket: one row per filing (unique filing_key per dump), all bureaus.
-- subsystem_code says which bureau/system ('SAT' space stations, 'SES' earth stations, 'ITC'
-- section 214 telecom, ...). The date columns are the filing lifecycle; a filed date with no
-- grant/deny/dismiss/surrender date is a pending application. Columns are the useful subset of
-- MAIN's 53 documented columns; signer/address/contact/fee plumbing stays in the raw zip.
CREATE TABLE IF NOT EXISTS raw_ibfs_filings (
    filing_key              BIGINT,
    callsign                TEXT,
    file_number             TEXT,
    subsystem_code          TEXT,
    status_code             TEXT,
    status_date             DATE,
    last_action             TEXT,
    last_action_date        DATE,
    date_filed              DATE,
    date_grant              DATE,
    date_deny               DATE,
    date_dismiss            DATE,
    date_surrender          DATE,
    date_begin              DATE,
    date_expire             DATE,
    date_last_update        DATE,
    app_type_code           TEXT,
    type_applicant_code     TEXT,
    class_of_station_code   TEXT,
    description             TEXT,
    ingest_run_id           BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    loaded_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_ibfs_filings_run ON raw_ibfs_filings (ingest_run_id);
CREATE INDEX IF NOT EXISTS raw_ibfs_filings_call ON raw_ibfs_filings (callsign);
CREATE INDEX IF NOT EXISTS raw_ibfs_filings_file_number ON raw_ibfs_filings (file_number);
CREATE INDEX IF NOT EXISTS raw_ibfs_filings_status ON raw_ibfs_filings (status_code);

-- IBFS SPACE_STATION reference table: US name, ITU name, orbit location. Landed exactly as
-- exported, duplicates included (raw layer reports what the source published); consumers dedupe.
-- us_name frequently embeds the FCC call sign in parentheses ('O3B-A (S2935)'), which is the
-- only join key back to MAIN.callsign that exists inside this dump. The source column named
-- 'verbose' lands as verbose_name because VERBOSE is unusable as a bare Postgres column name.
CREATE TABLE IF NOT EXISTS raw_ibfs_space_stations (
    space_station_key   BIGINT,
    us_name             TEXT,
    itu_name            TEXT,
    orbit_location      TEXT,
    verbose_name        TEXT,
    inactive_date       DATE,
    ingest_run_id       BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_ibfs_space_stations_run
    ON raw_ibfs_space_stations (ingest_run_id);
CREATE INDEX IF NOT EXISTS raw_ibfs_space_stations_us_name
    ON raw_ibfs_space_stations (us_name);

-- IBFS FREQUENCY: one row per authorized frequency assignment (unique frequency_key per dump).
-- Frequencies are in MHz ('00003700.00000000' is C-band downlink 3700 MHz), eirp in dBW,
-- emission is the ITU emission designator. The join path to a filing is
-- FREQUENCY.antenna_key -> ANTENNA.site_key -> SITE.filing_key; ANTENNA and SITE are not landed
-- yet, so antenna_key is kept as the hook for that future join.
CREATE TABLE IF NOT EXISTS raw_ibfs_frequencies (
    frequency_key       BIGINT,
    antenna_key         BIGINT,
    polarization_code   TEXT,
    eirp                DOUBLE PRECISION,
    eirp_density        DOUBLE PRECISION,
    emission            TEXT,
    frequency_lower     NUMERIC,
    frequency_upper     NUMERIC,
    trans_mode          TEXT,
    modulation          TEXT,
    ingest_run_id       BIGINT NOT NULL REFERENCES ingest_run (ingest_run_id),
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_ibfs_frequencies_run ON raw_ibfs_frequencies (ingest_run_id);
CREATE INDEX IF NOT EXISTS raw_ibfs_frequencies_antenna ON raw_ibfs_frequencies (antenna_key);

-- The pre-launch pipeline: space-station applications that have been filed but not resolved.
-- Space-station filings are subsystem_code = 'SAT'; equivalently file_number LIKE 'SAT%',
-- because the dump stores file numbers without dashes, so the web-UI form 'SAT-%' matches
-- NOTHING here (verified 2026-07-30: 0 of 141,086 file numbers contain a dash after SAT).
-- Pending is defined by dates, not status codes: date_filed set and none of the four terminal
-- outcome dates (grant, deny, dismiss, surrender) set. The dump's STATUS_CODE lookup shows
-- status_code is a WORKFLOW state, not a disposition: pending filings observed 2026-07-30
-- carried ATPN 'Action Taken Public Notice' (507), UNBLK 'Unblocked' (57), AFPN 'Accepted for
-- Filing Public Notice' (47), FPRVD 'Filed - payment received' (37), even A/C 'Action
-- Complete' (10), so the raw status_code is exposed rather than interpreted. Station names
-- attach by extracting the parenthesized call sign from us_name ('O3B-A (S2935)' -> S2935),
-- falling back to the whole us_name for old rows where us_name IS the call sign; one call sign
-- can map to several published names (renames), so names aggregate with ' / '. Roughly a
-- quarter of pending filings join (STAs on undeployed birds have no station row yet); the rest
-- keep NULL names, absence over guesses.
CREATE OR REPLACE VIEW v_fcc_pending_applications AS
WITH latest_filings AS (
    SELECT max(r.ingest_run_id) AS run_id FROM raw_ibfs_filings r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id WHERE i.status = 'ok'
),
latest_stations AS (
    SELECT max(r.ingest_run_id) AS run_id FROM raw_ibfs_space_stations r
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
    f.description
FROM raw_ibfs_filings f
JOIN latest_filings lf ON f.ingest_run_id = lf.run_id
LEFT JOIN stations s ON s.join_key = upper(f.callsign)
WHERE f.subsystem_code = 'SAT'
  AND f.date_filed IS NOT NULL
  AND f.date_grant IS NULL
  AND f.date_deny IS NULL
  AND f.date_dismiss IS NULL
  AND f.date_surrender IS NULL
ORDER BY f.date_filed DESC;
