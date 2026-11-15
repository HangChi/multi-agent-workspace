"""Deterministic Streamable HTTP fixture for feature 003 verification.

This fixture is test-only and intentionally has no production server startup path.
Run with an explicit ``--scenario`` and endpoint; tests can also import the
scenario payload helpers without binding a socket.
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROTOCOL_VERSION = "2025-06-18"


def _tools_page(cursor: str | None = None) -> dict[str, Any]:
    tools = [
        {
            "name": "search",
            "description": "Search the deterministic fixture corpus.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "slow_search",
            "description": "Waits before returning a result.",
            "inputSchema": {
                "type": "object",
                "properties": {"delay_ms": {"type": "integer", "minimum": 1}},
                "required": ["delay_ms"],
                "additionalProperties": False,
            },
        },
    ]
    if cursor == "page-2":
        return {"tools": [{"name": "error_search", "description": "Returns isError.", "inputSchema": {"type": "object"}}]}
    return {"tools": tools[:1], "nextCursor": "page-2"}


def handle_message(message: dict[str, Any], *, scenario: str = "normal") -> dict[str, Any]:
    request_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": True}}, "serverInfo": {"name": "fixture", "version": "1.0.0"}}}
    if method == "notifications/initialized":
        return {}
    if method == "tools/list":
        if scenario == "malformed":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "missing-schema"}]}}
        return {"jsonrpc": "2.0", "id": request_id, "result": _tools_page(message.get("params", {}).get("cursor"))}
    if method == "tools/call":
        name = message.get("params", {}).get("name")
        if scenario == "timeout" or name == "slow_search":
            delay_ms = int(message.get("params", {}).get("arguments", {}).get("delay_ms", 1000))
            time.sleep(delay_ms / 1000)
        if scenario == "tool-error" or name == "error_search":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"isError": True, "content": [{"type": "text", "text": "fixture tool error"}]}}
        if scenario == "oversized":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "X" * 300_000}]}}
        return {"jsonrpc": "2.0", "id": request_id, "result": {"isError": False, "content": [{"type": "text", "text": json.dumps({"ok": True, "tool": name})}]}}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}}


class Handler(BaseHTTPRequestHandler):
    server_version = "mcp-fixture/1.0"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        try:
            message = json.loads(self.rfile.read(length))
            payload = handle_message(message, scenario=self.server.scenario)  # type: ignore[attr-defined]
            notification = message.get("method", "").startswith("notifications/")
            body = b"" if notification else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(202 if notification else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self.send_error(400, str(exc))

    def log_message(self, *_args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", choices=("normal", "malformed", "tool-error", "oversized", "timeout"), default="normal")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.scenario = args.scenario  # type: ignore[attr-defined]
    server.serve_forever()


if __name__ == "__main__":
    main()
