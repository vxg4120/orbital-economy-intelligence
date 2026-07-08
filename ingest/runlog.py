"""The politeness/ledger core. Every network pull in ingest/ goes through here.

`polite_get` is the single choke point: it checks the `ingest_run` ledger for a recent
successful pull of the same source+endpoint and skips (logging `skipped_fresh`) if one exists
within `min_interval`. Otherwise it performs the HTTP GET, tags the ingest_run row it created
with the response so the caller can finish the run once it knows how many rows it landed.

Callers are responsible for calling `finish_run(..., status="ok")` once they've parsed and
landed the payload — `polite_get` only owns the fetch, the skip check, and the error path.
"""

import datetime as dt

import requests

USER_AGENT = "orbital-economy-intelligence/0.1 (portfolio project; polite; contact in repo)"
TIMEOUT_S = 120


def start_run(conn, source: str, endpoint: str) -> int:
    """Open a new ingest_run row and return its id. Caller must eventually finish_run() it."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ingest_run (source, endpoint, started_at) "
            "VALUES (%s, %s, now()) RETURNING ingest_run_id",
            (source, endpoint),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(
    conn,
    run_id: int,
    rows: int,
    bytes_dl: int,
    status: str,
    notes: str | None = None,
) -> None:
    """Close out an ingest_run row with its final outcome."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingest_run SET finished_at = now(), rows_ingested = %s, "
            "bytes_downloaded = %s, status = %s, notes = %s WHERE ingest_run_id = %s",
            (rows, bytes_dl, status, notes, run_id),
        )
    conn.commit()


def fresh_within(conn, source: str, endpoint: str, interval: dt.timedelta) -> bool:
    """True if an `ok` run for this source+endpoint finished within `interval` of now."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM ingest_run WHERE source = %s AND endpoint = %s AND status = 'ok' "
            "AND finished_at >= now() - %s LIMIT 1",
            (source, endpoint, interval),
        )
        return cur.fetchone() is not None


def polite_get(
    conn,
    source: str,
    endpoint: str,
    url: str,
    min_interval: dt.timedelta,
    **requests_kwargs,
):
    """GET `url`, but only if the source+endpoint pull isn't fresh.

    Returns the `requests.Response` on success, tagged with `.oei_run_id` (the ingest_run row
    this pull opened — the caller finishes it with the real row count) and `.oei_bytes` (bytes
    downloaded). Returns None if a fresh `ok` run already exists (a `skipped_fresh` row is
    logged for visibility, but no HTTP call is made). Raises and logs an `error` row on HTTP
    failure.
    """
    if fresh_within(conn, source, endpoint, min_interval):
        run_id = start_run(conn, source, endpoint)
        finish_run(conn, run_id, rows=0, bytes_dl=0, status="skipped_fresh")
        return None

    run_id = start_run(conn, source, endpoint)
    headers = dict(requests_kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", USER_AGENT)
    try:
        response = requests.get(url, timeout=TIMEOUT_S, headers=headers, **requests_kwargs)
        response.raise_for_status()
    except requests.RequestException as exc:
        finish_run(conn, run_id, rows=0, bytes_dl=0, status="error", notes=str(exc)[:2000])
        raise

    response.oei_run_id = run_id
    response.oei_bytes = len(response.content)
    return response
