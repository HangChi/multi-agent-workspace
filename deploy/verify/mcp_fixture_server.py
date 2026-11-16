"""Deterministic Streamable HTTP fixture for feature 003 verification.

This fixture is test-only and intentionally has no production server startup path.
Run with an explicit ``--scenario`` and endpoint; tests can also import the
scenario payload helpers without binding a socket.

The real Worker/HTTP/API deployment acceptance (T063) needs the fixture to be
reachable over the same production transport as any administrator-registered MCP
server, so this server optionally terminates TLS itself and publishes bounded
concurrency evidence on ``GET /__stats__``. Those counters are the only way the
acceptance can prove the per-server lease bound and the absence of retries
without reading Worker internals or raw service logs.

``peak_tools_in_flight`` counts calls a client is *still waiting for*, which is
the quantity the per-server lease bounds: FR-017 requires the bound to release
the call's resources when the deadline is reached. A handler still sleeping
after its client gave up therefore does not count.

``--client-deadline-seconds`` must be slightly *shorter* than the Worker's
configured call timeout, not equal to it: this window starts when the request
arrives, which is already after the Worker's deadline started (lease
acquisition, TCP/TLS handshake, ``initialize``). A window as long as the
Worker's would keep counting an abandoned call for that overhead, i.e. after its
lease was released and the slot was reused, which reads as one call over the
configured bound.
"""

from __future__ import annotations

import argparse
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
STATS_PATH = "/__stats__"


class _ToolCall:
    """One ``tools/call`` the fixture is serving, seen from the caller's side."""

    __slots__ = ("started", "ended")

    def __init__(self, started: float) -> None:
        self.started = started
        self.ended = False

    def live(self, now: float, deadline_s: float | None) -> bool:
        """Is a client still waiting on this call?

        FR-017 requires the concurrency bound to *release the call's resources*
        once the deadline is reached, so a handler still executing after its
        client gave up holds no lease. The timeout scenario deliberately sleeps
        far past the deadline, so counting such a handler would measure this
        fixture's thread pool rather than the product's concurrency: ten
        abandoned calls would look like ten concurrent ones.
        """
        if self.ended:
            return False
        return deadline_s is None or now - self.started < deadline_s


class FixtureStats:
    """Bounded, thread-safe counters; never records arguments, headers or bodies.

    JSON-RPC ids are only unique per client instance, so a repeated id is not
    evidence of a retry. The retry signal is the session accounting instead:
    every ``ExternalToolExecutor`` call opens exactly one MCP session, so one
    ``initialize`` and one ``tools/call`` per call, and no extra requests.
    """

    def __init__(self, client_deadline_s: float | None = None,
                 clock: Any = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._requests = 0
        self._tool_calls = 0
        self._in_flight = 0
        self._peak_in_flight = 0
        self._methods: dict[str, int] = {}
        self._tools: dict[str, int] = {}
        self._client_deadline_s = client_deadline_s
        self._clock = clock
        self._calls: dict[int, _ToolCall] = {}
        self._next_call_id = 0
        self._peak_calls_in_flight = 0
        self._started = clock()

    def begin(self, method: str) -> int | None:
        """Open a request; the returned token identifies it to ``end``."""
        with self._lock:
            self._requests += 1
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
            self._methods[method] = self._methods.get(method, 0) + 1
            if method != "tools/call":
                return None
            now = self._clock()
            self._next_call_id += 1
            token = self._next_call_id
            self._calls = {key: call for key, call in self._calls.items()
                           if call.live(now, self._client_deadline_s)}
            self._calls[token] = _ToolCall(now)
            # The live count only ever grows when a call begins, so measuring it
            # here -- after retiring what the deadline has expired -- is the
            # peak, not a sample of it.
            self._peak_calls_in_flight = max(self._peak_calls_in_flight, len(self._calls))
            return token

    def tool(self, name: Any) -> None:
        with self._lock:
            self._tool_calls += 1
            key = name if isinstance(name, str) and len(name) <= 100 else "unknown"
            self._tools[key] = self._tools.get(key, 0) + 1

    def end(self, method: str, token: int | None = None) -> None:
        with self._lock:
            self._in_flight -= 1
            if method != "tools/call":
                return
            if token is not None:
                # A call already retired by the deadline is gone; popping it here
                # would otherwise retire a different, still-live call.
                self._calls.pop(token, None)
                return
            for call in self._calls.values():
                if not call.ended:
                    call.ended = True
                    return

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests": self._requests,
                "tool_calls": self._tool_calls,
                "peak_in_flight": self._peak_in_flight,
                "peak_tools_in_flight": self._peak_calls_in_flight,
                "sessions": self._methods.get("initialize", 0),
                "methods": dict(sorted(self._methods.items())),
                "tools": dict(sorted(self._tools.items())),
                "uptime_s": round(self._clock() - self._started, 3),
            }


def _tools_page(cursor: str | None = None) -> dict[str, Any]:
    """Three pages, so discovery has to follow every cursor to see all tools."""
    pages = {
        None: ([{
            "name": "search",
            "description": "Search the deterministic fixture corpus.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
        }], "page-2"),
        "page-2": ([{
            "name": "slow_search",
            "description": "Waits before returning a result.",
            "inputSchema": {
                "type": "object",
                "properties": {"delay_ms": {"type": "integer", "minimum": 1}},
                "required": ["delay_ms"],
                "additionalProperties": False,
            },
        }], "page-3"),
        "page-3": ([{
            "name": "error_search",
            "description": "Returns isError.",
            "inputSchema": {"type": "object"},
        }], None),
    }
    tools, next_cursor = pages[cursor if cursor in pages else None]
    page: dict[str, Any] = {"tools": tools}
    if next_cursor is not None:
        page["nextCursor"] = next_cursor
    return page


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
    # Keep the original HTTP/1.0 connection-per-request semantics: the existing
    # TLS fixture test asserts its handler thread exits promptly on shutdown.

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        stats = getattr(self.server, "stats", None)
        if self.path.split("?", 1)[0] == STATS_PATH and stats is not None:
            self._json(200, stats.snapshot())
            return
        if self.path.split("?", 1)[0] == "/health":
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        stats = getattr(self.server, "stats", None)
        try:
            message = json.loads(self.rfile.read(length))
        except (TypeError, ValueError, json.JSONDecodeError):
            self.send_error(400, "invalid JSON-RPC request")
            return
        if not isinstance(message, dict):
            self.send_error(400, "invalid JSON-RPC request")
            return
        method = message.get("method")
        if not isinstance(method, str):
            self.send_error(400, "invalid JSON-RPC request")
            return
        if stats is not None:
            token = stats.begin(method)
            # Count a tool call when it arrives, not when the (possibly slow)
            # handler returns, so a client that gives up still sees its own call.
            if method == "tools/call":
                stats.tool(message.get("params", {}).get("name"))
        try:
            payload = handle_message(message, scenario=self.server.scenario)  # type: ignore[attr-defined]
            notification = method.startswith("notifications/")
            body = b"" if notification else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(202 if notification else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self.send_error(400, str(exc))
        finally:
            if stats is not None:
                stats.end(method, token)

    def log_message(self, *_args: Any) -> None:
        return


def build_server(host: str, port: int, scenario: str,
                 client_deadline_s: float | None = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.scenario = scenario  # type: ignore[attr-defined]
    server.stats = FixtureStats(client_deadline_s)  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", choices=("normal", "malformed", "tool-error", "oversized", "timeout"), default="normal")
    parser.add_argument("--tls-cert", default=None, help="PEM certificate; enables HTTPS when paired with --tls-key")
    parser.add_argument("--tls-key", default=None, help="PEM private key for --tls-cert")
    parser.add_argument("--ready-file", default=None, help="touch after the listener is bound")
    parser.add_argument("--client-deadline-seconds", type=float, default=None,
                        help="How long a caller waits for tools/call. A handler still "
                             "running past it has been abandoned by its client, so it no "
                             "longer counts against the per-server concurrency bound.")
    args = parser.parse_args()
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be supplied together")
    server = build_server(args.host, args.port, args.scenario, args.client_deadline_seconds)
    scheme = "http"
    if args.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    if args.ready_file:
        with open(args.ready_file, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"scheme": scheme, "port": args.port}))
    print(f"mcp-fixture listening on {scheme}://{args.host}:{args.port} scenario={args.scenario}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
