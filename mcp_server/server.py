"""MCP stdio server loop: newline-delimited JSON-RPC 2.0 over stdin/stdout.

Implements the subset of the Model Context Protocol a tools-only server needs
(initialize, ping, tools/list, tools/call), with handle_message() kept pure so
tests can drive the protocol without a subprocess. Query results are serialized
with default=str so Decimal / date / datetime values from postgres survive JSON.
"""

from __future__ import annotations

import json
import sys

from mcp_server.tools import TOOLS, ToolError

SERVER_INFO = {"name": "oei-bus-benchmarks", "version": "1.0.0"}
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
JSONRPC = "2.0"

_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _result(msg_id, result) -> dict:
    return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": JSONRPC, "id": msg_id, "error": {"code": code, "message": message}}


def _tools_payload() -> dict:
    return {
        "tools": [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in TOOLS
        ]
    }


def _call_tool(params: dict) -> dict:
    name = params.get("name")
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"unknown tool: {name!r}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ToolError("arguments must be an object")
    payload = tool["handler"](**arguments)
    text = json.dumps(payload, default=str, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def handle_message(msg: dict) -> dict | None:
    """Handle one JSON-RPC message; returns the response dict, or None for notifications."""
    msg_id = msg.get("id")
    method = msg.get("method")
    is_notification = "id" not in msg

    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else (
            SUPPORTED_PROTOCOL_VERSIONS[0])
        return _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, _tools_payload())
    if method == "tools/call":
        try:
            return _result(msg_id, _call_tool(msg.get("params") or {}))
        except ToolError as exc:
            # Tool-level failure: a successful RPC whose result flags the error, per MCP spec.
            return _result(msg_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        except TypeError as exc:  # unexpected argument names
            return _result(msg_id, {
                "content": [{"type": "text", "text": f"invalid arguments: {exc}"}],
                "isError": True,
            })
    if is_notification:  # notifications/initialized, notifications/cancelled, ...
        return None
    return _error(msg_id, -32601, f"method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps(_error(None, -32700, "parse error")), flush=True)
            continue
        response = handle_message(msg)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
