"""Minimal DB connection helpers. No ORM, no pooling."""

import os

import psycopg

DEFAULT_DATABASE_URL = "postgresql://oei:oei@localhost:5433/oei"


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_conn() -> psycopg.Connection:
    """Return a psycopg connection to DATABASE_URL, autocommit off (the default)."""
    return psycopg.connect(_database_url())


def get_autocommit_conn() -> psycopg.Connection:
    """Return a psycopg connection with autocommit on, for DDL/migrations."""
    return psycopg.connect(_database_url(), autocommit=True)
