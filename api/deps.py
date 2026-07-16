"""Request-scoped database dependency.

Yields a psycopg connection from the existing ``common.db.get_conn`` helper, configured for this
API's needs: rows as dicts (so routers read columns by name) and the transaction marked READ ONLY
so the read-only contract is enforced by Postgres itself, not just by convention. The connection
is closed (rolling back its read-only transaction) when the request finishes.
"""

import os
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

from common.db import get_conn

# The Two Skies bridge reads a SECOND database, ``exo`` (the exoplanet identity graph), alongside
# the satellite graph ``oei``. Overridable via env, defaulting to the same local cluster.
EXO_DATABASE_URL_DEFAULT = "postgresql://oei:oei@localhost:5433/exo"


def get_db() -> Iterator[psycopg.Connection]:
    conn = get_conn()
    try:
        conn.row_factory = dict_row
        # Applied to the transaction the first SELECT opens; belt-and-suspenders against writes.
        conn.read_only = True
        yield conn
    finally:
        conn.close()


def get_exo_db() -> Iterator[psycopg.Connection]:
    """Read-only connection to the exoplanet identity DB (``exo``) — the Two Skies bridge's second
    sky. A distinct database from the satellite graph (``oei``); this is its ONLY consumer.

    READ ONLY is enforced at the transaction level, deliberately: a Wave-2/Stage-1 writer may be
    active in ``exo`` while this endpoint runs, so it must never write or run a migration. Postgres
    MVCC lets these reads proceed against a consistent snapshot without blocking on that writer.
    The connection targets ``EXO_DATABASE_URL`` (falling back to the local default).
    """
    url = os.environ.get("EXO_DATABASE_URL", EXO_DATABASE_URL_DEFAULT)
    conn = psycopg.connect(url)
    try:
        conn.row_factory = dict_row
        conn.read_only = True
        yield conn
    finally:
        conn.close()


def get_write_db() -> Iterator[psycopg.Connection]:
    """A SEPARATE writable connection for the single mutating route (the gold-verdict write).

    Kept apart from ``get_db`` on purpose: the read-only contract on every other endpoint must never
    be weakened to accommodate the one writer. The route owns the commit; on any unhandled error the
    open transaction is rolled back as the connection closes, so a failed write is never half-applied.
    """
    conn = get_conn()
    try:
        conn.row_factory = dict_row
        yield conn
    finally:
        conn.close()
