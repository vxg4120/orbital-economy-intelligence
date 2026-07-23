"""MCP tool implementations: thin wrappers over the API's shared query helpers.

Each call opens its own short-lived read-only connection (rows as dicts), reusing
api/routers/buses.py so the MCP surface and the HTTP API can never drift apart on
definitions. Argument errors surface as ToolError (mapped to an MCP tool error by
the server loop), mirroring the HTTP layer's 4xx behavior.
"""

from __future__ import annotations

from fastapi import HTTPException

from api.routers.buses import METHODOLOGY, detail_payload, leaderboard_rows
from common.db import get_conn


class ToolError(Exception):
    """A user-facing tool failure (bad argument, unknown slug)."""


def _run(fn, *args):
    from psycopg.rows import dict_row

    conn = get_conn()
    try:
        conn.row_factory = dict_row
        conn.read_only = True
        return fn(conn, *args)
    except HTTPException as exc:  # bad group/sort/metric or unknown slug
        raise ToolError(exc.detail) from exc
    finally:
        conn.close()


def bus_benchmarks(
    group: str = "manufacturer",
    sort: str = "fleet",
    min_n: int = 5,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """Leaderboard of bus manufacturers or bus models with performance benchmarks."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    min_n = max(1, int(min_n))
    result = _run(lambda conn: leaderboard_rows(conn, group, sort, min_n, limit, offset))
    result["methodology_version"] = METHODOLOGY["version"]
    result["methodology"] = METHODOLOGY["doc_url"]
    return result


def bus_detail(slug: str, kind: str | None = None) -> dict:
    """One manufacturer or bus model: benchmarks, constituents, sample and provenance."""
    if not slug or not isinstance(slug, str):
        raise ToolError("slug is required")
    return _run(lambda conn: detail_payload(conn, slug.strip().lower(), kind))


TOOLS = [
    {
        "name": "bus_benchmarks",
        "description": (
            "Leaderboard of satellite bus manufacturers or bus models with provenance-tracked "
            "performance benchmarks: fleet size, median time-to-operational, station-keeping "
            "share and tightness, decayed share, median lifetime, 5-year disposal compliance, "
            "and GP behavior coverage. Definitions are versioned; every number is traceable "
            "to source rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "enum": ["manufacturer", "bus"],
                    "description": "Group rows by manufacturer (default) or bus model.",
                },
                "sort": {
                    "type": "string",
                    "enum": [
                        "fleet", "on_orbit", "active", "tto", "station_keeping", "sk_share",
                        "decayed_share", "lifetime", "compliance", "coverage", "name",
                    ],
                    "description": "Sort key (default fleet).",
                },
                "min_n": {
                    "type": "integer",
                    "description": "Minimum cohort size (default 5).",
                },
                "limit": {"type": "integer", "description": "Max rows (default 25, cap 200)."},
                "offset": {"type": "integer", "description": "Pagination offset."},
            },
        },
        "handler": bus_benchmarks,
    },
    {
        "name": "bus_detail",
        "description": (
            "Detail for one bus manufacturer or bus model by slug (as returned by "
            "bus_benchmarks): headline benchmarks, constituent bus models / orgs, a sample of "
            "member satellites with per-satellite metrics, and per-metric provenance and "
            "coverage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Manufacturer or bus slug."},
                "kind": {
                    "type": "string",
                    "enum": ["manufacturer", "bus"],
                    "description": "Disambiguate when a slug exists in both groups.",
                },
            },
            "required": ["slug"],
        },
        "handler": bus_detail,
    },
]
