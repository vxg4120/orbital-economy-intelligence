"""Space-Track client: session auth, rate-limited, backing off, ingest_run-ledgered.

Every fetch goes through the same `ingest_run` ledger as the rest of ingest/ (source
='spacetrack'), even though the rate limiter here is Space-Track-specific (min 3s between
requests — well under their 30/min cap — with exponential backoff on 429/5xx) rather than the
freshness-window model in runlog.polite_get, since Space-Track pulls are windowed backfills, not
"pull the whole thing again" snapshots.
"""

import os
import time

import requests

from ingest import runlog
from ingest.celestrak_gp import land_gp_rows

BASE_URL = "https://www.space-track.org"
LOGIN_URL = f"{BASE_URL}/ajaxauth/login"
GP_HISTORY_PATH = "/basicspacedata/query/class/gp_history"
DECAY_PATH = "/basicspacedata/query/class/decay"

BATCH_SIZE = 100
MIN_REQUEST_INTERVAL_S = 3.0
BACKOFF_BASE_S = 30.0
MAX_RETRIES = 3
# Space-Track gp_history queries can legitimately take minutes server-side (observed live:
# repeated ReadTimeouts at 120s on 100-id batches). Generous read timeout + timeouts are
# retryable below, same backoff as 429/5xx.
TIMEOUT_S = 300


class SpaceTrackAuthError(RuntimeError):
    """Raised when Space-Track credentials are missing or login fails."""


class SpaceTrackRateLimitError(requests.RequestException):
    """Space-Track throttled the query and returned an error stub with HTTP 200.

    A ``requests.RequestException`` on purpose: it must be caught by the same ``except
    requests.RequestException`` that ledgers a failed pull as status='error' and re-raises, so the
    caller (the backfill) fails LOUDLY on a window instead of silently landing zero rows.
    """


def _is_rate_limit_stub(resp: requests.Response) -> bool:
    """True if a 200 response is actually a Space-Track throttle stub, not real data.

    Under load Space-Track answers ``/basicspacedata`` queries with HTTP 200 and a body of
    ``[{"error": "You've violated your query rate limit. ..."}]`` -- an error masquerading as a
    successful (but empty-looking) result. ``land_gp_rows`` would treat that single row as a
    degraded stub (no NORAD_CAT_ID/EPOCH), skip it, and land 0 rows, so the window looks "done"
    with nothing landed. We detect the stub here and turn it into a retry/raise.

    Cheap gate first: a real GP payload is large and carries no top-level ``"error"`` key, while
    the stub is a ~260-byte object -- so we only JSON-parse when the raw bytes actually mention an
    error, then confirm structurally (first element is a dict whose only meaningful key is
    ``error``) so a coincidental substring in real data can never trip it.
    """
    if resp.status_code != 200 or b'"error"' not in resp.content:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    if isinstance(data, dict):
        data = [data]
    return bool(data) and isinstance(data[0], dict) and "error" in data[0]


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class SpaceTrackClient:
    def __init__(
        self,
        conn,
        identity: str | None = None,
        password: str | None = None,
        session=None,
        pre_request=None,
    ):
        self.conn = conn
        self.identity = identity if identity is not None else os.environ.get("SPACETRACK_IDENTITY")
        self.password = password if password is not None else os.environ.get("SPACETRACK_PASSWORD")
        if not self.identity or not self.password:
            raise SpaceTrackAuthError(
                "Space-Track credentials missing: set SPACETRACK_IDENTITY and "
                "SPACETRACK_PASSWORD (see .env.example) or pass identity/password explicitly."
            )
        self.session = session or requests.Session()
        self._logged_in = False
        self._last_request_at: float | None = None
        # Called before EVERY query attempt, including retries (429/5xx/timeout/stub). External
        # rate limiters must hook here, not around gp_history() calls: retries are real requests
        # against Space-Track's hourly cap, and a per-call pacer undercounts them (observed live:
        # ~450/hr actual vs a 260/hr per-call budget during a retry storm).
        self._pre_request = pre_request or (lambda: None)

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _login(self) -> None:
        if self._logged_in:
            return
        self._throttle()
        resp = self.session.post(
            LOGIN_URL,
            data={"identity": self.identity, "password": self.password},
            timeout=TIMEOUT_S,
            headers={"User-Agent": runlog.USER_AGENT},
        )
        resp.raise_for_status()
        self._logged_in = True

    def _request(self, url: str) -> requests.Response:
        self._login()
        attempt = 0
        while True:
            self._pre_request()
            self._throttle()
            try:
                resp = self.session.get(
                    url, timeout=TIMEOUT_S, headers={"User-Agent": runlog.USER_AGENT}
                )
            except (requests.Timeout, requests.ConnectionError):
                # Transient network/server slowness: retry with the same backoff as 429/5xx.
                if attempt >= MAX_RETRIES:
                    raise
                time.sleep(BACKOFF_BASE_S * (2**attempt))
                attempt += 1
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    resp.raise_for_status()
                time.sleep(BACKOFF_BASE_S * (2**attempt))
                attempt += 1
                continue
            resp.raise_for_status()
            if _is_rate_limit_stub(resp):
                # HTTP 200 but a throttle stub: retry with the same backoff as a 429, and RAISE
                # after MAX_RETRIES rather than return the stub -- otherwise the batch lands 0 rows
                # and the backfill checkpoints the window as done (the exact silent data loss that
                # left Amazon/ICEYE/Iridium/Spire with no gp_history despite "successful" pulls).
                if attempt >= MAX_RETRIES:
                    raise SpaceTrackRateLimitError(
                        "Space-Track query rate limit hit (HTTP 200 error stub) after "
                        f"{MAX_RETRIES} retries: {url}"
                    )
                time.sleep(BACKOFF_BASE_S * (2**attempt))
                attempt += 1
                continue
            return resp

    def _ledgered_batches(self, path: str, norad_ids: list[int], query_suffix: str):
        """Yield rows per NORAD-id batch, each pull logged in ingest_run.

        The ledger `endpoint` is the stable Space-Track class name ('gp_history', 'decay') — NOT
        the per-batch query URL, which carries 100 comma-joined NORAD ids and would otherwise turn
        the ledger into thousands of one-off rows. The full query URL is stashed in `notes` so the
        exact request is still recoverable for forensics.
        """
        endpoint = path.rsplit("/", 1)[-1]
        for batch in _chunk(norad_ids, BATCH_SIZE):
            norad_list = ",".join(str(n) for n in batch)
            url = f"{BASE_URL}{path}/NORAD_CAT_ID/{norad_list}{query_suffix}"
            run_id = runlog.start_run(self.conn, "spacetrack", endpoint)
            try:
                resp = self._request(url)
            except requests.RequestException as exc:
                runlog.finish_run(
                    self.conn, run_id, rows=0, bytes_dl=0, status="error", notes=str(exc)[:2000]
                )
                raise
            rows = resp.json()
            runlog.finish_run(
                self.conn, run_id, rows=len(rows), bytes_dl=len(resp.content), status="ok",
                notes=url,
            )
            yield rows

    def gp_history(self, norad_ids: list[int], created_since: str, created_before: str):
        """Backfill gp_history, batched (100 NORAD ids/request) and windowed by CREATION_DATE.
        Yields parsed OMM-shaped dict rows; land them with `land_gp_history`."""
        suffix = (
            f"/CREATION_DATE/{created_since}--{created_before}/"
            "orderby/CREATION_DATE asc/format/json"
        )
        for rows in self._ledgered_batches(GP_HISTORY_PATH, norad_ids, suffix):
            yield from rows

    def decay(self, norad_ids: list[int]) -> list[dict]:
        """Decay messages, format=json. Landing is deferred to Phase 2 — kept thin here."""
        rows_out: list[dict] = []
        for rows in self._ledgered_batches(DECAY_PATH, norad_ids, "/format/json"):
            rows_out.extend(rows)
        return rows_out


def land_gp_history(conn, rows: list[dict]) -> int:
    """Land Space-Track gp_history rows into gp_elements, source='spacetrack_gp_history'."""
    return land_gp_rows(conn, rows, source="spacetrack_gp_history")
