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


@pytest.fixture(scope="session")
def _database_reachable():
    """One connection attempt per session, closed immediately.

    Held open it would be a second connection during the concurrent matview refresh that
    tests/test_latest_elements.py spawns, so this probes and lets go."""
    try:
        conn = get_conn()
    except psycopg.Error:
        return False
    conn.close()
    return True


@pytest.fixture(autouse=True)
def _skip_db_marked_without_a_database(request):
    """A db-marked test skips when there is no database, whether or not it takes db_conn.

    Without this the marker only means "deselected by the fast job": a db-marked test that
    reaches the database some other way (a subprocess, a TestClient) still ran in the full job
    and failed on connection refused, which reads as a broken test rather than a missing
    database. The marker is the declaration; this makes it the guarantee."""
    # The probe is resolved INSIDE the marker check, never as a parameter: an autouse fixture
    # that takes _database_reachable would make every unmarked test open a connection, which is
    # exactly what the fast job exists to avoid. With an unparseable DATABASE_URL that turned
    # seven database-free tests into errors.
    if not request.node.get_closest_marker("db"):
        return
    if not request.getfixturevalue("_database_reachable"):
        pytest.skip("database not reachable at DATABASE_URL")


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
