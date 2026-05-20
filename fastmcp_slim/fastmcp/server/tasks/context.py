"""Task context and scoping for background task execution.

Determines authorization scope (``get_task_scope``), manages the context
snapshot that is captured at task submission and restored in workers
(``TaskContextSnapshot``), and maintains in-process registries for live
sessions and servers.
"""

from __future__ import annotations

import json
import logging
import weakref
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastmcp.server.tasks.keys import parse_task_key, task_redis_prefix

try:
    from docket import TaskKey
except ImportError:

    def TaskKey() -> str:  # type: ignore[no-redef]
        # Stub so this module stays importable without the fastmcp[tasks]
        # extra. ``restore_task_snapshot`` is only ever invoked inside a
        # Docket worker, where the real ``docket.TaskKey`` sentinel is
        # always present.
        return ""


if TYPE_CHECKING:
    from docket import Docket
    from mcp.server.session import ServerSession

    from fastmcp.server.server import FastMCP

_logger = logging.getLogger(__name__)


def get_task_scope() -> str | None:
    """Get the authorization scope for task isolation.

    Returns the raw scope identifier for the current access token, or
    ``None`` when no auth context is present (anonymous tasks).

    The scope is composed as ``client_id|sub`` when the token carries a
    ``sub`` claim — necessary for fixed-OAuth servers where ``client_id`` is
    shared across all users — and falls back to ``client_id`` alone for
    DCR/CIMD flows where the client identity is already per-user.

    Encoding for Redis/Docket keys happens at the boundary in ``keys.py``;
    this function returns the raw value.
    """
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        return None
    sub = token.claims.get("sub") if token.claims else None
    if sub:
        return f"{token.client_id}|{sub}"
    return token.client_id


@dataclass(frozen=True, slots=True)
class TaskContextInfo:
    """Information about the current background task context.

    Returned by ``get_task_context()`` when running inside a Docket worker.
    Contains identifiers needed to communicate with the MCP session.
    """

    task_id: str
    """The MCP task ID (server-generated UUID)."""

    task_scope: str | None
    """The authorization scope that owns this task, or ``None`` if anonymous."""


def get_task_context() -> TaskContextInfo | None:
    """Get the current task context if running inside a background task worker.

    This function extracts task information from the Docket execution context.
    Returns None if not running in a task context (e.g., foreground execution).

    Returns:
        TaskContextInfo with task_id and task_scope, or None if not in a task.
    """
    from fastmcp.server.dependencies import is_docket_available

    if not is_docket_available():
        return None

    from docket.dependencies import current_execution

    try:
        execution = current_execution.get()
        key_parts = parse_task_key(execution.key)
        return TaskContextInfo(
            task_id=key_parts["client_task_id"],
            task_scope=key_parts["task_scope"],
        )
    except LookupError:
        return None
    except (ValueError, KeyError):
        return None


@dataclass(frozen=True, slots=True)
class TaskContextSnapshot:
    """All context data snapshotted at task-submission time.

    Stored as a single Redis key per task, restored once in the worker.
    """

    access_token_json: str | None = None
    http_headers: dict[str, str] | None = None
    origin_request_id: str | None = None
    session_id: str | None = None

    @classmethod
    def capture(cls) -> TaskContextSnapshot:
        """Capture current context for background task execution."""
        from fastmcp.server.dependencies import (
            get_access_token,
            get_context,
            get_http_headers,
        )

        access_token = get_access_token()
        ctx = get_context()
        request_context = ctx.request_context
        try:
            session_id = ctx.session_id
        except RuntimeError:
            session_id = None
        return cls(
            access_token_json=(
                access_token.model_dump_json() if access_token else None
            ),
            http_headers=get_http_headers(include_all=True) or None,
            origin_request_id=(
                str(request_context.request_id) if request_context is not None else None
            ),
            session_id=session_id,
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> TaskContextSnapshot:
        """Deserialize from JSON stored in Redis."""
        if isinstance(raw, bytes):
            raw = raw.decode()
        parsed = json.loads(raw)
        headers = parsed.get("http_headers")
        if isinstance(headers, dict):
            headers = {str(k).lower(): str(v) for k, v in headers.items()}
        return cls(
            access_token_json=parsed.get("access_token_json"),
            http_headers=headers,
            origin_request_id=parsed.get("origin_request_id"),
            session_id=parsed.get("session_id"),
        )

    def to_json(self) -> str:
        """Serialize to JSON for Redis storage."""
        return json.dumps(
            {
                "access_token_json": self.access_token_json,
                "http_headers": self.http_headers,
                "origin_request_id": self.origin_request_id,
                "session_id": self.session_id,
            }
        )

    async def save(
        self,
        docket: Docket,
        task_scope: str | None,
        task_id: str,
        ttl_seconds: int,
    ) -> None:
        """Store this snapshot as a single Redis key."""
        key = docket.key(f"{task_redis_prefix(task_scope)}:{task_id}:snapshot")
        async with docket.redis() as redis:
            await redis.set(key, self.to_json(), ex=ttl_seconds)


# Cache keyed by task_id so stale entries from previous tasks in the same
# asyncio context are automatically ignored (Docket workers may reuse contexts).
_task_snapshot: ContextVar[tuple[str, TaskContextSnapshot] | None] = ContextVar(
    "task_snapshot", default=None
)


def _remember_snapshot(task_id: str, snapshot: TaskContextSnapshot) -> None:
    """Bind a snapshot to the current asyncio context under ``task_id``.

    Nothing outside this task's context sees it; stale entries left in a
    reused context are ignored on recall.
    """
    _task_snapshot.set((task_id, snapshot))


def _recall_snapshot(task_id: str) -> TaskContextSnapshot | None:
    """Return the snapshot bound for ``task_id`` in the current context.

    Returns ``None`` if nothing is bound, or if the bound entry belongs to
    a different task (a stale leftover from a reused asyncio context).
    """
    cached = _task_snapshot.get()
    if cached is not None:
        cached_task_id, snapshot = cached
        if cached_task_id == task_id:
            return snapshot
    return None


def get_task_session_id() -> str | None:
    """Get the session_id for the current background task, if available.

    Reads the cached snapshot set by the worker-level restore dependency.
    Returns None if not in a task context or the snapshot wasn't restored.
    """
    task_info = get_task_context()
    if task_info is None:
        return None
    snapshot = _recall_snapshot(task_info.task_id)
    return snapshot.session_id if snapshot else None


async def restore_task_snapshot(key: str = TaskKey()) -> None:
    """Worker-level Docket dependency that restores the task-context snapshot.

    Runs before each fastmcp-owned task, populating the snapshot ContextVar
    so user code — and any task-scoped dependency like ``_CurrentContext`` —
    sees a ready snapshot without touching Redis itself.  All Redis I/O
    goes through Docket's async client, so cluster URLs and the memory://
    backend work transparently (#3897).  Failures are non-fatal: the task
    still runs, and sync helpers return ``None`` as they would have before
    the snapshot was captured.
    """
    try:
        parts = parse_task_key(key)
    except ValueError:
        # Non-fastmcp key (e.g. docket scheduler internals) — nothing to do.
        return

    from fastmcp.server.dependencies import _current_docket, get_server

    try:
        docket = get_server()._docket
    except RuntimeError:
        docket = None
    if docket is None:
        docket = _current_docket.get()
    if docket is None:
        return

    task_scope = parts["task_scope"]
    task_id = parts["client_task_id"]
    try:
        async with docket.redis() as redis:
            raw = await redis.get(
                docket.key(f"{task_redis_prefix(task_scope)}:{task_id}:snapshot")
            )
        if raw is None:
            return
        _remember_snapshot(task_id, TaskContextSnapshot.from_json(raw))
    except Exception:
        _logger.warning("Failed to restore task snapshot for %s", key, exc_info=True)


# In-process optimization: when the Docket worker runs in the same process as
# the MCP server, we can hand background tasks a live ServerSession so they can
# call session methods directly (e.g. send_notification).  In distributed
# deployments where workers are separate processes, these registries will be
# empty and the worker's Context will have session=None — that's fine, because
# elicitation and notifications have Redis-based fallbacks that work across
# process boundaries (see notifications.py and elicitation.py).

_task_sessions: dict[str, weakref.ref[ServerSession]] = {}


def register_task_session(session_id: str, session: ServerSession) -> None:
    """Register a session for in-process background task access.

    Called automatically when a task is submitted to Docket. The session is
    stored as a weakref so it doesn't prevent garbage collection when the
    client disconnects.
    """
    _task_sessions[session_id] = weakref.ref(session)


def get_task_session(session_id: str) -> ServerSession | None:
    """Get a registered session by ID if still alive.

    Returns None in distributed workers where the session lives in another
    process — callers must handle this gracefully.
    """
    ref = _task_sessions.get(session_id)
    if ref is None:
        return None
    session = ref()
    if session is None:
        _task_sessions.pop(session_id, None)
    return session


_task_server_map: OrderedDict[str, weakref.ref[FastMCP]] = OrderedDict()
_TASK_SERVER_MAP_MAX_SIZE = 10_000


def register_task_server(task_id: str, server: FastMCP) -> None:
    """Register the server for a background task.

    Called at task-submission time so that background workers can resolve
    the correct (child) server for mounted tasks.
    """
    _task_server_map[task_id] = weakref.ref(server)
    while len(_task_server_map) > _TASK_SERVER_MAP_MAX_SIZE:
        _task_server_map.popitem(last=False)


def get_task_server(task_id: str) -> FastMCP | None:
    """Get the registered server for a background task, if still alive."""
    ref = _task_server_map.get(task_id)
    if ref is None:
        return None
    server = ref()
    if server is None:
        _task_server_map.pop(task_id, None)
    return server
