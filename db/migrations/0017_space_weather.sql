-- Space weather indices (CelesTrak consolidated daily file, back to 1957).
--
-- One row per calendar day per ingest run: the eight 3-hourly planetary Kp values (stored as
-- Kp x 10, the file's convention), the eight Ap values with the daily average, and the F10.7
-- solar radio flux family (observed, adjusted, and 81-day centered/trailing means). The file
-- also carries INTERPOLATED and PREDICTED rows past the last observed day; f107_data_type
-- says which is which, and consumers filter rather than the landing.
--
-- Why this matters here: geomagnetic storms heat the thermosphere and raise density at LEO
-- altitudes, so every index in this table has a measurable echo in sat_daily's semi-major
-- axis deltas. The drag join lives in metrics/space_weather.sql (it needs the sat_daily
-- continuous aggregate, which migrations must not depend on).
CREATE TABLE IF NOT EXISTS raw_celestrak_sw (
    sw_date            DATE NOT NULL,
    bsrn               INTEGER,
    nd                 INTEGER,
    kp1                INTEGER,
    kp2                INTEGER,
    kp3                INTEGER,
    kp4                INTEGER,
    kp5                INTEGER,
    kp6                INTEGER,
    kp7                INTEGER,
    kp8                INTEGER,
    kp_sum             INTEGER,
    ap1                INTEGER,
    ap2                INTEGER,
    ap3                INTEGER,
    ap4                INTEGER,
    ap5                INTEGER,
    ap6                INTEGER,
    ap7                INTEGER,
    ap8                INTEGER,
    ap_avg             INTEGER,
    cp                 NUMERIC,
    c9                 INTEGER,
    isn                INTEGER,
    f107_obs           NUMERIC,
    f107_adj           NUMERIC,
    f107_data_type     TEXT,
    f107_obs_center81  NUMERIC,
    f107_obs_last81    NUMERIC,
    f107_adj_center81  NUMERIC,
    f107_adj_last81    NUMERIC,
    ingest_run_id      BIGINT NOT NULL REFERENCES ingest_run(ingest_run_id),
    loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_celestrak_sw_date ON raw_celestrak_sw (sw_date);
CREATE INDEX IF NOT EXISTS raw_celestrak_sw_run ON raw_celestrak_sw (ingest_run_id);

-- One row per day from the latest OK landing. kp_max/kp_sum are rescaled to real Kp units;
-- storm_level is NOAA's G scale (G1 at Kp 5 through G5 at Kp 9), NULL on quiet days.
CREATE OR REPLACE VIEW v_space_weather_daily AS
WITH latest AS (
    SELECT max(r.ingest_run_id) AS run_id
    FROM raw_celestrak_sw r
    JOIN ingest_run i ON i.ingest_run_id = r.ingest_run_id
    WHERE i.status = 'ok'
)
SELECT DISTINCT ON (r.sw_date)
    r.sw_date                                           AS day,
    r.bsrn,
    (GREATEST(r.kp1, r.kp2, r.kp3, r.kp4,
              r.kp5, r.kp6, r.kp7, r.kp8) / 10.0)       AS kp_max,
    (r.kp_sum / 10.0)                                   AS kp_sum,
    r.ap_avg,
    GREATEST(r.ap1, r.ap2, r.ap3, r.ap4,
             r.ap5, r.ap6, r.ap7, r.ap8)                AS ap_max,
    r.isn,
    r.f107_obs,
    r.f107_adj,
    r.f107_obs_center81,
    r.f107_data_type,
    CASE
        WHEN GREATEST(r.kp1, r.kp2, r.kp3, r.kp4, r.kp5, r.kp6, r.kp7, r.kp8) >= 90 THEN 'G5'
        WHEN GREATEST(r.kp1, r.kp2, r.kp3, r.kp4, r.kp5, r.kp6, r.kp7, r.kp8) >= 80 THEN 'G4'
        WHEN GREATEST(r.kp1, r.kp2, r.kp3, r.kp4, r.kp5, r.kp6, r.kp7, r.kp8) >= 70 THEN 'G3'
        WHEN GREATEST(r.kp1, r.kp2, r.kp3, r.kp4, r.kp5, r.kp6, r.kp7, r.kp8) >= 60 THEN 'G2'
        WHEN GREATEST(r.kp1, r.kp2, r.kp3, r.kp4, r.kp5, r.kp6, r.kp7, r.kp8) >= 50 THEN 'G1'
        ELSE NULL
    END                                                 AS storm_level
FROM raw_celestrak_sw r, latest
WHERE r.ingest_run_id = latest.run_id
ORDER BY r.sw_date;
