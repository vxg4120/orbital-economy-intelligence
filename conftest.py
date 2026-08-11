import psycopg
import pytest

from api import cache
from common.db import get_conn


def pytest_configure(config):
    config.addinivalue_line("markers", "db: test requires a reachable DATABASE_URL")


@pytest.fixture(autouse=True, scope="session")
def _no_warm_cache():
    """Compute endpoint payloads live for the whole test session.

    The /api/stats and /api/congestion handlers are served from a warm in-process cache in the
    running app (see api/cache.py). The endpoint tests are data-quality gates: they inject rows
    -- often uncommitted, through a get_db dependency override -- and assert the payload reflects
    them. A cached payload would ignore the injected data and the gate would pass without testing
    anything, so caching is off under pytest.
    """
    cache.set_enabled(False)
    yield
    cache.set_enabled(True)


@pytest.fixture
def db_conn():
    try:
        conn = get_conn()
    except psycopg.OperationalError:
        pytest.skip("database not reachable at DATABASE_URL")
        return
    try:
        yield conn
    finally:
        conn.close()
