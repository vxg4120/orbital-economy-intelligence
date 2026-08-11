"""Warm in-process cache for the whole-catalog aggregates behind the Overview view.

Two endpoints dominate first-paint latency, measured warm against production:

    /api/stats       ~8.9 s   six sequential aggregates (status conflicts 3.2 s, decay 2.0 s,
                              stale owners 1.7 s, coverage 1.3 s, headline counts 0.7 s)
    /api/congestion ~11.4 s   DISTINCT ON (norad_id) over the 10.2M-row gp_elements hypertable,
                              reading every row to yield 17k latest-per-satellite

Both recompute from scratch on every page load, so Overview -- the first thing any visitor sees --
spins for roughly nine seconds. The data underneath only moves when the nightly ingest runs
(07:10 and 19:10), which makes per-request recomputation pure waste.

A plain TTL cache does not fix this on its own. Traffic here is sporadic, so most visitors would
arrive after the TTL lapsed and pay full price anyway; the cache would help nobody except during
a burst. So the value is kept warm on a timer *independent of traffic*: computed once at startup
and refreshed on an interval, which means a request essentially never waits on the database.

Staleness is bounded by REFRESH_S and is honest at this cadence -- the source data changes twice a
day, so a value up to fifteen minutes old is indistinguishable from a fresh one.

Single uvicorn worker (api.main runs without --workers), so a process-local value is the whole
cache and no cross-process coordination is needed. This mirrors the candidate cache in
routers/reachability.py, which caches for the same reason with the same primitives.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.rows import dict_row

from common.db import get_conn

log = logging.getLogger(__name__)

# The source data moves twice a day; fifteen minutes keeps the value warm without polling hard.
REFRESH_S = 900.0
# A failed refresh retries sooner than the normal cadence rather than waiting out a full interval.
RETRY_S = 60.0


def _read_only_conn() -> psycopg.Connection:
    """A dict-row, read-only connection for the refresher thread.

    The request path gets its connection from api.deps.get_db; a background thread cannot use a
    request-scoped dependency, so it opens its own with the same read-only contract.
    """
    conn = get_conn()
    conn.row_factory = dict_row
    conn.read_only = True
    return conn


class WarmCache:
    """One cached payload, recomputed on a background timer.

    ``get`` never blocks on the database once a value exists: a stale value is served as-is while
    the refresher catches up. Only a caller arriving before the first successful compute pays for
    one inline, which keeps a cold start correct rather than empty.
    """

    def __init__(self, name: str, compute: Callable[[psycopg.Connection], Any]):
        self._name = name
        self._compute = compute
        self._lock = threading.Lock()
        self._value: Any = None
        self._has_value = False
        self._started = False

    def get(self, db: psycopg.Connection) -> Any:
        """Serve the warm value, or compute against ``db`` when caching is off.

        ``db`` is the request-scoped connection. It is used only on the disabled path, which is
        what keeps the endpoint tests honest: tests/test_api_stats.py injects an uncommitted row
        through a get_db dependency override and asserts the payload reflects it, so a cache that
        ignored the request connection would quietly turn that data-quality gate into a no-op.
        """
        if not _enabled:
            return self._compute(db)
        with self._lock:
            if self._has_value:
                return self._value
        # No value yet (first request during a cold start, or every refresh so far has failed).
        return self._refresh()

    def _refresh(self) -> Any:
        conn = _read_only_conn()
        try:
            started = time.monotonic()
            value = self._compute(conn)
        finally:
            conn.close()
        with self._lock:
            self._value = value
            self._has_value = True
        log.info("warm cache %s refreshed in %.2fs", self._name, time.monotonic() - started)
        return value

    def _loop(self) -> None:
        while True:
            try:
                self._refresh()
                delay = REFRESH_S
            except Exception:
                # A refresh failure must never kill the thread: the last good value keeps serving
                # and we retry. Logged with a stack trace so a persistent failure is visible.
                log.exception("warm cache %s refresh failed; serving last good value", self._name)
                delay = RETRY_S
            time.sleep(delay)

    def start(self) -> None:
        """Begin refreshing in the background. Idempotent."""
        with self._lock:
            if self._started:
                return
            self._started = True
        thread = threading.Thread(target=self._loop, name=f"warm-{self._name}", daemon=True)
        thread.start()


_REGISTRY: list[WarmCache] = []

# Caching is on in the served application and off under pytest (see the autouse fixture in
# conftest.py). The endpoint tests are live data-quality gates: they inject rows and assert the
# payload reflects them, which only works if the handler really queries the request connection.
_enabled = True


def set_enabled(enabled: bool) -> None:
    """Turn warm caching on or off process-wide."""
    global _enabled
    _enabled = enabled


def register(name: str, compute: Callable[[psycopg.Connection], Any]) -> WarmCache:
    """Create a cache and enrol it for startup warming."""
    cache = WarmCache(name, compute)
    _REGISTRY.append(cache)
    return cache


def start_all() -> None:
    """Start every registered refresher. Called from the app startup hook."""
    for cache in _REGISTRY:
        cache.start()
