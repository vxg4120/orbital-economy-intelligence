"""CelesTrak GP (current element sets, CCSDS OMM via FORMAT=json) loader.

Raw landing only: fields are mapped 1:1 onto `gp_elements` columns, no semantic interpretation.
`land_gp_rows` is shared with `ingest/spacetrack_client.py` (gp_history has the same OMM field
names), so the parsing/coercion logic lives in exactly one place.
"""

import datetime as dt
import logging

from ingest import runlog

logger = logging.getLogger(__name__)

SOURCE = "celestrak"
ENDPOINT_TMPL = "gp_{group}"
URL_TMPL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=json"
MIN_INTERVAL = dt.timedelta(hours=2)
DEFAULT_GROUP = "active"

GP_INSERT_SQL = """
INSERT INTO gp_elements (
    norad_id, epoch, mean_motion, eccentricity, inclination,
    ra_of_asc_node, arg_of_pericenter, mean_anomaly, bstar,
    rev_at_epoch, source, creation_date
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (norad_id, epoch, source) DO NOTHING
"""


def _parse_utc(value) -> dt.datetime | None:
    if value in (None, ""):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _to_float(value) -> float | None:
    return None if value in (None, "") else float(value)


def _to_int(value) -> int | None:
    return None if value in (None, "") else int(value)


def land_gp_rows(conn, rows: list[dict], source: str) -> int:
    """INSERT OMM rows into gp_elements tagged with `source`. Duplicates (same norad/epoch/
    source) are silently skipped via ON CONFLICT DO NOTHING — re-running the same pull is safe.
    """
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                GP_INSERT_SQL,
                (
                    int(row["NORAD_CAT_ID"]),
                    _parse_utc(row["EPOCH"]),
                    _to_float(row.get("MEAN_MOTION")),
                    _to_float(row.get("ECCENTRICITY")),
                    _to_float(row.get("INCLINATION")),
                    _to_float(row.get("RA_OF_ASC_NODE")),
                    _to_float(row.get("ARG_OF_PERICENTER")),
                    _to_float(row.get("MEAN_ANOMALY")),
                    _to_float(row.get("BSTAR")),
                    _to_int(row.get("REV_AT_EPOCH")),
                    source,
                    _parse_utc(row.get("CREATION_DATE")),
                ),
            )
    conn.commit()
    return len(rows)


def run(conn, group: str = DEFAULT_GROUP) -> int:
    """Pull one GP group (default and normally only `active`) and land it into gp_elements
    with source='celestrak_gp'. Never requests any other group implicitly."""
    endpoint = ENDPOINT_TMPL.format(group=group)
    url = URL_TMPL.format(group=group)
    resp = runlog.polite_get(conn, SOURCE, endpoint, url, MIN_INTERVAL)
    if resp is None:
        logger.info("gp(%s): skipped, fresh run within %s", group, MIN_INTERVAL)
        return 0

    rows = resp.json()
    n = land_gp_rows(conn, rows, source="celestrak_gp")
    runlog.finish_run(conn, resp.oei_run_id, rows=n, bytes_dl=resp.oei_bytes, status="ok")
    return n
