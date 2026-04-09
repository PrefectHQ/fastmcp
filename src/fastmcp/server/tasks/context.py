"""Task context management for background task execution.

Provides task scoping, context snapshotting, and session/server registries
for Docket background workers.
"""

from __future__ import annotations

import json
import logging
import weakref
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from docket import Docket
    from mcp.server.session import ServerSession

    from fastmcp.server.server import FastMCP

_logger = logging.getLogger(__name__)


def get_task_scope() -> str:
    """Get the authorization scope for task isolation.

    Returns the URL-encoded ``client_id`` from the current access token when
    auth is configured, or the ``"_"`` sentinel when no auth is present.
    The value is safe to use directly in Redis keys and Docket task keys.
    """
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is not None:
        return quote(token.client_id, safe="")
    return "_"


@dataclass(frozen=True, slots=True)
class TaskContextInfo:
    """Information about the current background task context.

    Returned by ``get_task_context()`` when running inside a Docket worker.
    Contains identifiers needed to communicate with the MCP session.
    """

    task_id: str
    """The MCP task ID (server-generated UUID)."""

    task_scope: str
    """The authorization scope that owns this task."""


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

    from fastmcp.server.tasks.keys import parse_task_key

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
        task_scope: str,
        task_id: str,
        ttl_seconds: int,
    ) -> None:
        """Store this snapshot as a single Redis key."""
        key = docket.key(f"fastmcp:task:{task_scope}:{task_id}:snapshot")
        async with docket.redis() as redis:
            await redis.set(key, self.to_json(), ex=ttl_seconds)


# Cache keyed by task_id so stale entries from previous tasks in the same
# asyncio context are automatically ignored (Docket workers may reuse contexts).
_task_snapshot: ContextVar[tuple[str, TaskContextSnapshot] | None] = ContextVar(
    "task_snapshot", default=None
)


def _set_cached_snapshot(task_id: str, snapshot: TaskContextSnapshot) -> None:
    """Cache a snapshot keyed by task_id."""
    _task_snapshot.set((task_id, snapshot))


def _get_cached_snapshot(task_id: str) -> TaskContextSnapshot | None:
    """Get cached snapshot if it belongs to this task."""
    cached = _task_snapshot.get()
    if cached is not None:
        cached_task_id, snapshot = cached
        if cached_task_id == task_id:
            return snapshot
    return None


def _snapshot_redis_key(task_scope: str, task_id: str) -> str:
    """Build the Redis key suffix for a task snapshot."""
    return f"fastmcp:task:{task_scope}:{task_id}:snapshot"


async def _load_task_snapshot_async(
    task_scope: str, task_id: str
) -> TaskContextSnapshot | None:
    """Load task context snapshot from Redis (async) and cache it.

    Idempotent — returns the cached value if already loaded for this task.
    """
    cached = _get_cached_snapshot(task_id)
    if cached is not None:
        return cached

    from fastmcp.server.dependencies import _current_docket, get_server

    try:
        docket = get_server()._docket
    except RuntimeError:
        docket = None
    if docket is None:
        docket = _current_docket.get()
    if docket is None:
        return None

    try:
        async with docket.redis() as redis:
            raw = await redis.get(docket.key(_snapshot_redis_key(task_scope, task_id)))
        if raw is None:
            return None
        snapshot = TaskContextSnapshot.from_json(raw)
        _set_cached_snapshot(task_id, snapshot)
        return snapshot
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        _logger.warning(
            "Failed to load task snapshot for %s:%s",
            task_scope,
            task_id,
            exc_info=True,
        )
        return None


def _get_task_snapshot_sync() -> TaskContextSnapshot | None:
    """Get the task snapshot using only sync operations.

    Fallback chain:
    1. ContextVar cache (keyed by task_id, set by async or sync loaders)
    2. Sync Redis GET (works for both memory:// and real Redis)
    """
    task_info = get_task_context()
    if task_info is None:
        return None

    cached = _get_cached_snapshot(task_info.task_id)
    if cached is not None:
        return cached

    return _load_task_snapshot_sync(task_info.task_scope, task_info.task_id)


def _load_task_snapshot_sync(
    task_scope: str, task_id: str
) -> TaskContextSnapshot | None:
    """Load snapshot via sync Redis.

    For memory:// backends (fakeredis), shares the same FakeServer instance
    that Docket uses so data is accessible. For real Redis, creates a standard
    sync connection.
    """
    try:
        from docket.dependencies import current_docket as _docket_cv

        docket = _docket_cv.get()
    except (LookupError, ImportError):
        return None
    if docket is None:
        return None

    try:
        sync_redis = _get_sync_redis(docket.url)
        raw = sync_redis.get(docket.key(_snapshot_redis_key(task_scope, task_id)))
        if raw is None:
            return None
        snapshot = TaskContextSnapshot.from_json(raw)
        _set_cached_snapshot(task_id, snapshot)
        return snapshot
    except (OSError, json.JSONDecodeError, KeyError, ValueError, ImportError):
        _logger.warning(
            "Failed to load task snapshot via sync Redis for %s:%s",
            task_scope,
            task_id,
            exc_info=True,
        )
        return None


def _get_sync_redis(url: str) -> Any:
    """Get a sync Redis client that shares the same backend as Docket.

    For memory:// URLs, connects to the same fakeredis FakeServer instance
    so data written by the async Docket client is visible. For real Redis
    URLs, creates a standard sync connection.
    """
    from docket._redis import get_memory_server

    server = get_memory_server(url)
    if server is not None:
        from fakeredis import FakeRedis

        return FakeRedis(server=server)

    from redis import Redis

    return Redis.from_url(url)


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
