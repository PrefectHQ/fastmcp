"""Bounded message capture for the FastMCP Apps development log panel."""

from __future__ import annotations

import contextlib
import json
import time
from collections import deque
from typing import Any

_MAX_LOG_ENTRIES = 1000
_MAX_LOG_BYTES = 8 * 1024 * 1024
_MAX_LOG_ENTRY_BYTES = 256 * 1024
_MAX_TRACKED_REQUESTS = 1000
_MAX_JSONRPC_ID_LENGTH = 128


class _MessageLog:
    """Bounded in-memory buffer of MCP JSON-RPC messages for the dev UI."""

    def __init__(
        self,
        *,
        max_entries: int = _MAX_LOG_ENTRIES,
        max_bytes: int = _MAX_LOG_BYTES,
        max_entry_bytes: int = _MAX_LOG_ENTRY_BYTES,
        max_tracked_requests: int = _MAX_TRACKED_REQUESTS,
    ) -> None:
        self._entries: deque[dict[str, Any]] = deque()
        self._entry_sizes: deque[int] = deque()
        self._max_entries = max(0, max_entries)
        self._max_bytes = max(0, max_bytes)
        # Leave room for the method name and entry metadata when deciding
        # whether to truncate a body.
        self._max_entry_bytes = max(1024, max_entry_bytes)
        self._max_tracked_requests = max(0, max_tracked_requests)
        self._retained_bytes = 0
        self._counter = 0
        self._request_methods: dict[int | str, str] = {}
        self._request_times: dict[int | str, float] = {}

    @staticmethod
    def _method_name(body: dict[str, Any]) -> str:
        method = body.get("method", "unknown")
        if not isinstance(method, str):
            return "unknown"
        try:
            method[:64].encode("utf-8")
        except UnicodeEncodeError:
            return "unknown"
        return method[:64]

    def _bounded_body(self, body: dict[str, Any]) -> dict[str, Any]:
        """Copy JSON bodies and replace unusually large payloads with a marker."""
        try:
            encoded = json.dumps(
                body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError, UnicodeEncodeError):
            return {"_fastmcp_log_truncated": True, "reason": "not JSON serializable"}

        if len(encoded) > self._max_entry_bytes - 768:
            return {
                "_fastmcp_log_truncated": True,
                "original_size_bytes": len(encoded),
            }
        # Detach retained data from mutable request objects and normalize it to
        # the same JSON values that the API will return.
        return json.loads(encoded)

    def _append(self, entry: dict[str, Any]) -> None:
        entry["body"] = self._bounded_body(entry["body"])
        encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        size = len(encoded)
        self._entries.append(entry)
        self._entry_sizes.append(size)
        self._retained_bytes += size

        while self._entries and (
            len(self._entries) > self._max_entries
            or self._retained_bytes > self._max_bytes
        ):
            self._entries.popleft()
            self._retained_bytes -= self._entry_sizes.popleft()

    def _remember_request(
        self, jsonrpc_id: int | str | None, method: str, timestamp: float
    ) -> None:
        if (
            jsonrpc_id is None
            or isinstance(jsonrpc_id, bool)
            or not isinstance(jsonrpc_id, (int, str))
            or len(str(jsonrpc_id)) > _MAX_JSONRPC_ID_LENGTH
            or self._max_tracked_requests == 0
        ):
            return

        # Repeated IDs replace their older timing record and move to the newest
        # position in the insertion-ordered dictionaries.
        self._request_methods.pop(jsonrpc_id, None)
        self._request_times.pop(jsonrpc_id, None)
        while len(self._request_times) >= self._max_tracked_requests:
            oldest_id = next(iter(self._request_times))
            self._request_times.pop(oldest_id, None)
            self._request_methods.pop(oldest_id, None)
        self._request_methods[jsonrpc_id] = method
        self._request_times[jsonrpc_id] = timestamp

    def log_request(self, body: dict[str, Any]) -> None:
        method = self._method_name(body)
        jsonrpc_id = body.get("id")
        timestamp = time.time()
        self._remember_request(jsonrpc_id, method, timestamp)
        self._counter += 1
        self._append(
            {
                "id": self._counter,
                "timestamp": timestamp,
                "direction": "request",
                "method": method,
                "body": body,
            }
        )

    def log_response(self, body: dict[str, Any]) -> None:
        # Server-initiated notifications have "method" but no "id"
        if "method" in body and "id" not in body:
            self._counter += 1
            self._append(
                {
                    "id": self._counter,
                    "timestamp": time.time(),
                    "direction": "notification",
                    "method": self._method_name(body),
                    "body": body,
                }
            )
            return

        jsonrpc_id = body.get("id")
        method = (
            self._request_methods.pop(jsonrpc_id, None)
            if jsonrpc_id is not None
            else None
        )
        request_time = (
            self._request_times.pop(jsonrpc_id, None)
            if jsonrpc_id is not None
            else None
        )
        timestamp = time.time()
        duration_ms = (
            round((timestamp - request_time) * 1000, 1) if request_time else None
        )
        self._counter += 1
        self._append(
            {
                "id": self._counter,
                "timestamp": timestamp,
                "direction": "response",
                "method": method,
                "body": body,
                "duration_ms": duration_ms,
            }
        )

    def get_since(self, since_id: int = 0) -> list[dict[str, Any]]:
        return [entry for entry in self._entries if entry["id"] > since_id]

    def log_bridge(self, body: dict[str, Any]) -> None:
        method = self._method_name(body)
        self._counter += 1
        self._append(
            {
                "id": self._counter,
                "timestamp": time.time(),
                "direction": "bridge",
                "method": method,
                "body": body,
            }
        )

    def clear(self) -> None:
        self._entries.clear()
        self._entry_sizes.clear()
        self._retained_bytes = 0
        self._request_methods.clear()
        self._request_times.clear()


def _log_response_bytes(log: _MessageLog, raw: bytes, content_type: str) -> None:
    """Parse accumulated proxy response bytes and log as message entries."""
    if not raw:
        return
    try:
        if "text/event-stream" in content_type:
            for line in raw.decode("utf-8", errors="replace").splitlines():
                if line.startswith("data: "):
                    with contextlib.suppress(json.JSONDecodeError):
                        log.log_response(json.loads(line[6:]))
        else:
            body = json.loads(raw)
            if isinstance(body, list):
                for item in body:
                    log.log_response(item)
            else:
                log.log_response(body)
    except (json.JSONDecodeError, TypeError):
        pass
