"""Read-only MCP server over the Orbital Economy identity graph (Bus Benchmarks tools).

Dependency-free implementation of the Model Context Protocol's stdio transport (the official
python SDK does not install on this environment's toolchain), exposing:

* bus_benchmarks: the manufacturer / bus-model leaderboard with sort, cohort-floor and paging
* bus_detail: one manufacturer or bus model with constituents, satellite sample and provenance

Run with `make mcp` or `.venv/bin/python -m mcp_server`.
"""
