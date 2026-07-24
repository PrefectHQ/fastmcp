"""A minimal MCP server spoken over stdio, using only the standard library.

This is a **test fixture**, not a real server. It exists so that subprocess
lifecycle tests (keep-alive, crash recovery, PID identity) can spawn many
short-lived servers without paying for `import fastmcp` in every child
process. Importing fastmcp and constructing a `FastMCP` instance costs
roughly 0.7s per spawn; this script starts in roughly 0.03s.

It implements only what those tests exercise: the `initialize` handshake,
`tools/list`, and `tools/call` for two trivial tools. Anything that needs
real FastMCP semantics (tool serialization, error handling, structured
output shapes) must use a real FastMCP server instead.

The response shapes below were captured from the wire of a real FastMCP
stdio server so that `CallToolResult.data` deserializes identically.

Usage:

    python minimal_stdio_server.py [--exit-after-calls N]

With `--exit-after-calls N`, the `pid` tool schedules a clean `os._exit(0)`
shortly after its Nth invocation, simulating a server that shuts itself
down mid-session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from typing import Any

INT_OUTPUT_SCHEMA: dict[str, Any] = {
    "properties": {"result": {"type": "integer"}},
    "required": ["result"],
    "type": "object",
    "x-fastmcp-wrap-result": True,
}

STR_OUTPUT_SCHEMA: dict[str, Any] = {
    "properties": {"result": {"type": "string"}},
    "required": ["result"],
    "type": "object",
    "x-fastmcp-wrap-result": True,
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "pid",
        "description": "Gets PID of server",
        "inputSchema": {
            "properties": {},
            "type": "object",
            "additionalProperties": False,
        },
        "outputSchema": INT_OUTPUT_SCHEMA,
    },
    {
        "name": "echo",
        "description": "Echoes the message back",
        "inputSchema": {
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "type": "object",
            "additionalProperties": False,
        },
        "outputSchema": STR_OUTPUT_SCHEMA,
    },
]

METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def _wrapped_result(value: int | str) -> dict[str, Any]:
    """Mirror how FastMCP reports a scalar return value on the wire."""
    return {
        "_meta": {"fastmcp": {"wrap_result": True}},
        "content": [{"type": "text", "text": str(value)}],
        "isError": False,
        "structuredContent": {"result": value},
    }


class MinimalServer:
    def __init__(self, exit_after_calls: int | None) -> None:
        self.exit_after_calls = exit_after_calls
        self.pid_call_count = 0

    def send(self, message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    def reply(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def reply_error(self, request_id: Any, code: int, message: str) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def handle_initialize(self, request_id: Any, params: dict[str, Any]) -> None:
        # Echo the client's requested version back. The client rejects any
        # version it did not ask for, and echoing keeps this fixture working
        # across SDK protocol bumps without edits.
        protocol_version = params.get("protocolVersion")
        self.reply(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "MinimalStdioServer", "version": "1.0.0"},
            },
        )

    def handle_tools_call(self, request_id: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments") or {}

        if name == "pid":
            self.pid_call_count += 1
            pid = os.getpid()
            if (
                self.exit_after_calls is not None
                and self.pid_call_count >= self.exit_after_calls
            ):
                # Reply first, then exit shortly after, so the client sees a
                # successful call followed by an unannounced clean shutdown.
                self.reply(request_id, _wrapped_result(pid))
                threading.Timer(0.1, lambda: os._exit(0)).start()
                return
            self.reply(request_id, _wrapped_result(pid))
            return

        if name == "echo":
            message = arguments.get("message")
            if not isinstance(message, str):
                self.reply_error(request_id, INVALID_PARAMS, "message must be a string")
                return
            self.reply(request_id, _wrapped_result(message))
            return

        self.reply_error(request_id, INVALID_PARAMS, f"Unknown tool: {name}")

    def handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if request_id is None:
            # Notification (e.g. notifications/initialized) — nothing to send.
            return

        if method == "initialize":
            self.handle_initialize(request_id, params)
        elif method == "ping":
            self.reply(request_id, {})
        elif method == "tools/list":
            self.reply(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            self.handle_tools_call(request_id, params)
        else:
            self.reply_error(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}")

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self.handle(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exit-after-calls", type=int, default=None)
    parsed = parser.parse_args()
    MinimalServer(exit_after_calls=parsed.exit_after_calls).run()


if __name__ == "__main__":
    main()
