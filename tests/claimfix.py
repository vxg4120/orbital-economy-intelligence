"""Seed a feed's claim for a test (claim table, migration 0023): the crosswalk link that makes
it the satellite's, the open claim, and the feed's progress row."""

import datetime as dt

ID_TYPE = {"satcat": "norad", "gcat": "gcat_id", "ucs": "ucs_row"}
T0 = dt.datetime(2026, 10, 1, 7, 10, tzinfo=dt.UTC)


def seed_claim(cur, sat_id, source, attribute, value, run, key=None, at=T0):
    key = str(sat_id) if key is None else key
    if sat_id is not None:
        cur.execute(
            "INSERT INTO satellite_identifier (satellite_id, id_type, id_value, source) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (sat_id, ID_TYPE[source], key, source),
        )
    cur.execute(
        "INSERT INTO claim (source, source_key, attribute, value, first_run, observed_from) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (source, key, attribute, value, run, at),
    )
    cur.execute(
        "INSERT INTO claim_progress (source, last_run, observed_at) VALUES (%s, %s, %s) "
        "ON CONFLICT (source) DO UPDATE SET last_run = greatest(claim_progress.last_run, "
        "EXCLUDED.last_run), observed_at = greatest(claim_progress.observed_at, EXCLUDED.observed_at)",
        (source, run, at),
    )
