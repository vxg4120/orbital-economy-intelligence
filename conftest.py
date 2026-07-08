import psycopg
import pytest

from common.db import get_conn


def pytest_configure(config):
    config.addinivalue_line("markers", "db: test requires a reachable DATABASE_URL")


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
