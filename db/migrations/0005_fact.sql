-- Fact layer: SPEC.md §6, verbatim. create_hypertable guarded with if_not_exists => TRUE for
-- idempotent re-runs.

CREATE TABLE gp_elements (
    norad_id           BIGINT NOT NULL,
    epoch              TIMESTAMPTZ NOT NULL,
    mean_motion        DOUBLE PRECISION NOT NULL,  -- rev/day
    eccentricity       DOUBLE PRECISION NOT NULL,
    inclination        DOUBLE PRECISION,
    ra_of_asc_node     DOUBLE PRECISION,
    arg_of_pericenter  DOUBLE PRECISION,
    mean_anomaly       DOUBLE PRECISION,
    bstar              DOUBLE PRECISION,
    rev_at_epoch       BIGINT,
    source             TEXT NOT NULL,              -- celestrak_gp | spacetrack_gp_history | supgp
    creation_date      TIMESTAMPTZ,
    -- Derived (Earth mu = 398600.4418 km^3/s^2, Re = 6378.137 km).
    -- Postgres generated columns cannot reference each other, hence the repetition.
    semi_major_axis_km DOUBLE PRECISION GENERATED ALWAYS AS (
        power(398600.4418 / power(mean_motion * 2 * pi() / 86400.0, 2), 1.0/3.0)
    ) STORED,
    apogee_km          DOUBLE PRECISION GENERATED ALWAYS AS (
        power(398600.4418 / power(mean_motion * 2 * pi() / 86400.0, 2), 1.0/3.0)
        * (1 + eccentricity) - 6378.137
    ) STORED,
    perigee_km         DOUBLE PRECISION GENERATED ALWAYS AS (
        power(398600.4418 / power(mean_motion * 2 * pi() / 86400.0, 2), 1.0/3.0)
        * (1 - eccentricity) - 6378.137
    ) STORED,
    PRIMARY KEY (norad_id, epoch, source)
);
SELECT create_hypertable('gp_elements', 'epoch', if_not_exists => TRUE);
ALTER TABLE gp_elements SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'norad_id',
    timescaledb.compress_orderby   = 'epoch'
);
SELECT add_compression_policy('gp_elements', INTERVAL '30 days');
