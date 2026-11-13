"""The politeness/ledger core. Every network pull in ingest/ goes through here.

`polite_get` is the single choke point: it checks the `ingest_run` ledger for a recent
successful pull of the same source+endpoint and skips (logging `skipped_fresh`) if one exists
within `min_interval`. Otherwise it performs the HTTP GET, tags the ingest_run row it created
with the response so the caller can finish the run once it knows how many rows it landed.

Callers are responsible for calling `finish_run(..., status="ok")` once they've parsed and
landed the payload — `polite_get` only owns the fetch, the skip check, and the error path.
"""

import datetime as dt
import time

import requests

USER_AGENT = "orbital-economy-intelligence/0.1 (portfolio project; polite; contact in repo)"
TIMEOUT_S = 120
# A host that does not accept the connection within this is down; waiting the full read timeout
# for it made each failed attempt cost two minutes.
CONNECT_TIMEOUT_S = 30

# A server error, a refused or dropped connection (including one dropped partway through the
# body) or a timeout is retried twice within the same run, after these waits. On 2026-11-13 at 19:11 UTC CelesTrak answered 500 to the GP and SupGP pulls, which had
# one attempt each, so the night's element sets were lost and an alert went out; the same URL
# answered 200 when checked 40 minutes later. A 4xx is never retried: CelesTrak answers 403 to a
# client it has blocked for pulling too often, and asking again would only extend the block.
RETRY_STATUSES = frozenset({500, 502, 503, 504})
RETRY_WAITS_S = (30, 120)
# ChunkedEncodingError is a connection reset partway through the body, the likeliest failure for
# the large GP, SATCAT and GCAT downloads; it is not a ConnectionError subclass.
_RETRY_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
_sleep = time.sleep  # the tests record the waits instead of sleeping

# The interval for a feed that refreshes once a day. The nightly runs at 07:10 and 19:10 UTC and
# fresh_within counts from the last pull's finished_at, so a 24-hour interval left the same slot
# the next day "fresh" by the seconds the download took: satcat and GCAT refreshed every 36 hours
# (every third slot) until 2026-10-11. Anything between one slot (12 hours) and a day works;
# 20 hours leaves room for a late or slow run on either side.
DAILY = dt.timedelta(hours=20)


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
    this pull opened, which the caller finishes with the real row count), `.oei_bytes` (bytes
    downloaded) and `.oei_retries` (what each failed attempt hit, empty when the first one
    worked). Returns None if a fresh `ok` run already exists (a `skipped_fresh` row is logged
    for visibility, but no HTTP call is made). A server error or a connection failure is
    retried after each of RETRY_WAITS_S, all within the one run row; when the last attempt
    fails too, or on any other HTTP error, it raises and logs an `error` row.
    """
    if fresh_within(conn, source, endpoint, min_interval):
        run_id = start_run(conn, source, endpoint)
        finish_run(conn, run_id, rows=0, bytes_dl=0, status="skipped_fresh")
        return None

    run_id = start_run(conn, source, endpoint)
    headers = dict(requests_kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", USER_AGENT)
    retries: list[str] = []
    try:
        for wait in (*RETRY_WAITS_S, None):
            try:
                response = requests.get(
                    url, timeout=(CONNECT_TIMEOUT_S, TIMEOUT_S), headers=headers, **requests_kwargs
                )
            except _RETRY_ERRORS as exc:
                if wait is None:
                    raise
                retries.append(type(exc).__name__)
            else:
                if response.status_code not in RETRY_STATUSES or wait is None:
                    response.raise_for_status()
                    break
                retries.append(f"HTTP {response.status_code}")
            print(f"[{source}] {endpoint}: {retries[-1]}, retrying in {wait}s", flush=True)
            _sleep(wait)
    except requests.RequestException as exc:
        note = str(exc)
        if retries:
            note = f"after {len(retries)} retries ({', '.join(retries)}): {note}"
        finish_run(conn, run_id, rows=0, bytes_dl=0, status="error", notes=note[:2000])
        raise

    response.oei_run_id = run_id
    response.oei_bytes = len(response.content)
    response.oei_retries = retries
    return response
