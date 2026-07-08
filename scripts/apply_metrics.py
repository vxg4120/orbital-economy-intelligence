"""Applies metrics/*.sql (continuous aggregate + benchmark views) idempotently.

Runs on an autocommit connection: TimescaleDB continuous aggregates (CREATE MATERIALIZED VIEW
... WITH (timescaledb.continuous) and add_continuous_aggregate_policy) cannot run inside a
transaction block, same constraint as scripts/migrate.py. Unlike migrate.py there is no
schema_migrations-style tracking table here -- idempotency is baked into the SQL itself
(CREATE MATERIALIZED VIEW IF NOT EXISTS, CREATE OR REPLACE VIEW, if_not_exists => TRUE on the
refresh policy), so re-running this script is always safe and always applies both files.

Order matters: caggs.sql (sat_daily) must apply before benchmark_views.sql, whose views select
from sat_daily.

Unlike migrate.py's migrations (which never mix a cagg statement with anything else in one
file), caggs.sql contains both a CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous) and
an add_continuous_aggregate_policy() call. PostgreSQL implicitly wraps a multi-statement simple
query (several statements sent in one execute() call, semicolon-separated) in a transaction
block regardless of the client's autocommit setting -- and a continuous aggregate cannot be
created inside that implicit block either. So each file is split into individual statements and
executed one at a time, each its own simple query, each genuinely autocommitted.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from common.db import get_autocommit_conn

METRICS_DIR = pathlib.Path(__file__).resolve().parent.parent / "metrics"
METRICS_FILES = ["caggs.sql", "benchmark_views.sql"]


def _statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements on top-level semicolons.

    Strips `--` line comments first (a prose comment can itself contain a semicolon, e.g. a
    sentence break -- that must not be mistaken for a statement boundary), then splits the
    remaining code on ';'. Naive but safe here: none of our metrics SQL contains '--' or ';'
    inside string literals or dollar-quoted bodies.
    """
    code_only = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [stmt.strip() for stmt in code_only.split(";") if stmt.strip()]


def main() -> None:
    conn = get_autocommit_conn()
    try:
        for filename in METRICS_FILES:
            path = METRICS_DIR / filename
            for statement in _statements(path.read_text()):
                with conn.cursor() as cur:
                    cur.execute(statement)
            print(f"applied {filename}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
