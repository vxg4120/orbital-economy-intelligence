"""MCP server tests: protocol handling (no DB) + tool execution (DB-backed)."""

import json

import pytest

from mcp_server import server, tools


def _req(msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def test_initialize_and_tools_list_need_no_db():
    r = server.handle_message(_req(1, "initialize", {"protocolVersion": "2025-06-18"}))
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["serverInfo"]["name"] == "oei-bus-benchmarks"
    assert "tools" in r["result"]["capabilities"]

    # Unknown requested version: server answers with its own latest supported version.
    r = server.handle_message(_req(2, "initialize", {"protocolVersion": "1999-01-01"}))
    assert r["result"]["protocolVersion"] == server.SUPPORTED_PROTOCOL_VERSIONS[0]

    r = server.handle_message(_req(3, "tools/list"))
    names = [t["name"] for t in r["result"]["tools"]]
    assert names == ["bus_benchmarks", "bus_detail"]
    for tool in r["result"]["tools"]:
        assert tool["description"] and tool["inputSchema"]["type"] == "object"


def test_notifications_and_unknown_methods():
    assert server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    r = server.handle_message(_req(9, "bogus/method"))
    assert r["error"]["code"] == -32601
    assert server.handle_message(_req(10, "ping"))["result"] == {}


@pytest.mark.db
def test_bus_benchmarks_tool(db_conn):
    result = tools.bus_benchmarks(limit=5, min_n=5)
    assert result["total"] > 0
    assert 0 < len(result["rows"]) <= 5
    for row in result["rows"]:
        assert row["fleet_total"] >= 5
        assert row["slug"] and row["name"]
    assert result["methodology_version"]

    with pytest.raises(tools.ToolError):
        tools.bus_benchmarks(sort="bogus")


@pytest.mark.db
def test_bus_detail_tool_and_call_roundtrip(db_conn):
    top = tools.bus_benchmarks(limit=1)["rows"][0]
    detail = tools.bus_detail(slug=top["slug"])
    assert detail["kind"] == "manufacturer"
    assert detail["benchmark"]["slug"] == top["slug"]
    assert detail["satellites_sample"]

    # Full protocol round trip: the tools/call result is MCP-shaped, JSON-serializable text.
    r = server.handle_message(_req(4, "tools/call", {
        "name": "bus_detail", "arguments": {"slug": top["slug"]},
    }))
    assert r["result"]["isError"] is False
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["benchmark"]["slug"] == top["slug"]

    with pytest.raises(tools.ToolError):
        tools.bus_detail(slug="zz-no-such-slug")


@pytest.mark.db
def test_tool_error_maps_to_mcp_tool_error(db_conn):
    r = server.handle_message(_req(5, "tools/call", {
        "name": "bus_benchmarks", "arguments": {"group": "bogus"},
    }))
    assert r["result"]["isError"] is True
    assert "group" in r["result"]["content"][0]["text"]

    r = server.handle_message(_req(6, "tools/call", {"name": "no_such_tool"}))
    assert r["result"]["isError"] is True
