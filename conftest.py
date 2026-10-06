import psycopg
import pytest

from api import cache
from common.db import get_conn


def pytest_configure(config):
    config.addinivalue_line("markers", "db: test requires a reachable DATABASE_URL")
    config.addinivalue_line(
        "markers",
        "graph: test asserts on the populated identity graph; skipped when the database is empty",
    )


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


@pytest.fixture(scope="session")
def _graph_populated(_database_reachable):
    """Whether the identity graph holds any satellite, probed once and let go.

    About ninety tests assert on what the real graph contains (published buses, the ISS track,
    pending filings). On a migrated but empty database, which is what CI has, they could only
    fail, and the db job had never been green because of it."""
    if not _database_reachable:
        return False
    conn = get_conn()
    try:
        return conn.execute("SELECT EXISTS (SELECT 1 FROM satellite)").fetchone()[0]
    except psycopg.Error:
        return False  # not migrated: no graph either
    finally:
        conn.close()


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
    # A graph test is always a db test too (tests/test_marker_hygiene.py enforces it), so this
    # probe is reached only by tests that were going to connect anyway.
    if request.node.get_closest_marker("graph") and not request.getfixturevalue(
        "_graph_populated"
    ):
        pytest.skip("the identity graph at DATABASE_URL is empty")


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
