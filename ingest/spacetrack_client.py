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
TIMEOUT_S = 120


class SpaceTrackAuthError(RuntimeError):
    """Raised when Space-Track credentials are missing or login fails."""


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class SpaceTrackClient:
    def __init__(self, conn, identity: str | None = None, password: str | None = None, session=None):
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
            self._throttle()
            resp = self.session.get(url, timeout=TIMEOUT_S, headers={"User-Agent": runlog.USER_AGENT})
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    resp.raise_for_status()
                time.sleep(BACKOFF_BASE_S * (2**attempt))
                attempt += 1
                continue
            resp.raise_for_status()
            return resp

    def _ledgered_batches(self, path: str, norad_ids: list[int], query_suffix: str):
        """Yield (rows, endpoint) per NORAD-id batch, each pull logged in ingest_run."""
        for batch in _chunk(norad_ids, BATCH_SIZE):
            norad_list = ",".join(str(n) for n in batch)
            endpoint = f"{path}/NORAD_CAT_ID/{norad_list}{query_suffix}"
            url = BASE_URL + endpoint
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
                self.conn, run_id, rows=len(rows), bytes_dl=len(resp.content), status="ok"
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
