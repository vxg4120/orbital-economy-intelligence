"""Tiny migration runner.

Applies db/migrations/*.sql in filename order on an autocommit connection (TimescaleDB DDL like
create_hypertable and continuous aggregates cannot run inside a transaction block). Records
applied filenames in schema_migrations so re-runs are no-ops. Executes each file's full content in
a single execute() call — psycopg allows multiple statements per execute on autocommit
connections.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.db import get_autocommit_conn

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "db" / "migrations"

CREATE_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def main() -> None:
    conn = get_autocommit_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_SCHEMA_MIGRATIONS)
            cur.execute("SELECT filename FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not migration_files:
            print("No migration files found.")
            return

        for path in migration_files:
            if path.name in applied:
                print(f"skip  {path.name} (already applied)")
                continue
            sql = path.read_text()
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
            print(f"apply {path.name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
