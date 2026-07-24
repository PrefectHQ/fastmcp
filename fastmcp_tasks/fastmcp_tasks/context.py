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

from fastmcp_tasks.keys import (
    leg_number_from_key,
    parse_task_key,
    task_redis_prefix,
)

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

    from fastmcp.server.context import Context
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
    from fastmcp_tasks.dependencies import is_docket_available

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


def get_task_leg_number() -> int:
    """Return the current leg number of the running task (1 outside a re-entry).

    Each re-entry after client input runs as a fresh Docket execution under a
    per-leg key; the capture wrapper reads this to scope a leg's outstanding
    input requests so successive legs never collide in Redis.
    """
    from fastmcp_tasks.dependencies import is_docket_available

    if not is_docket_available():
        return 1

    from docket.dependencies import current_execution

    try:
        return leg_number_from_key(current_execution.get().key)
    except LookupError:
        return 1


@dataclass(frozen=True, slots=True)
class TaskContextSnapshot:
    """All context data snapshotted at task-submission time.

    Stored as a single Redis key per task, restored once in the worker.
    """

    access_token_json: str | None = None
    http_headers: dict[str, str] | None = None
    origin_request_id: str | None = None
    session_id: str | None = None
    owning_tool_name: str | None = None
    owning_tool_version: str | None = None

    @classmethod
    def capture(
        cls,
        owning_tool_name: str | None = None,
        owning_tool_version: str | None = None,
    ) -> TaskContextSnapshot:
        """Capture current context for background task execution.

        ``owning_tool_name``/``owning_tool_version`` identify the exact tool the
        call targeted. A remote worker (separate process) cannot reach the
        submitting process's server map, so it re-resolves the owning (child)
        server from this name and version against the root — see
        ``make_task_context``. The version matters when two versions of the same
        mounted tool name live on different child servers.
        """
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
            owning_tool_name=owning_tool_name,
            owning_tool_version=owning_tool_version,
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
            owning_tool_name=parsed.get("owning_tool_name"),
            owning_tool_version=parsed.get("owning_tool_version"),
        )

    def to_json(self) -> str:
        """Serialize to JSON for Redis storage."""
        return json.dumps(
            {
                "access_token_json": self.access_token_json,
                "http_headers": self.http_headers,
                "origin_request_id": self.origin_request_id,
                "session_id": self.session_id,
                "owning_tool_name": self.owning_tool_name,
                "owning_tool_version": self.owning_tool_version,
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

    from fastmcp.server.dependencies import get_server
    from fastmcp_tasks.dependencies import _current_docket

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
        snapshot = TaskContextSnapshot.from_json(raw)
        _remember_snapshot(task_id, snapshot)
        # Restore the ambient request context (auth token, headers) so core's
        # get_access_token()/get_http_headers() see the submitting caller inside
        # the worker, exactly as a normal request would.
        _apply_snapshot_to_context(snapshot)
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
_TASK_SESSION_CONNECTION_REF = "_fastmcp_task_session_ref"
_TASK_SESSION_CLEANUP_REGISTERED = "_fastmcp_task_session_cleanup_registered"


def _remove_task_session(session_id: str, ref: weakref.ref[ServerSession]) -> None:
    if _task_sessions.get(session_id) is ref:
        _task_sessions.pop(session_id)


def register_task_session(session_id: str, session: ServerSession) -> None:
    """Register a session for in-process background task access.

    Called automatically when a task is submitted to Docket. The session is
    stored as a weakref so it doesn't prevent garbage collection when the
    client disconnects.
    """

    session_ref = weakref.ref(
        session, lambda ref: _remove_task_session(session_id, ref)
    )
    _task_sessions[session_id] = session_ref

    connection = getattr(session, "_connection", None)
    if connection is None:
        return

    state = connection.state
    state[_TASK_SESSION_CONNECTION_REF] = (session_id, session_ref)
    if state.get(_TASK_SESSION_CLEANUP_REGISTERED):
        return

    def remove_connection_session() -> None:
        registered = state.pop(_TASK_SESSION_CONNECTION_REF, None)
        if registered is not None:
            registered_session_id, registered_ref = registered
            _remove_task_session(registered_session_id, registered_ref)

    connection.exit_stack.callback(remove_connection_session)
    state[_TASK_SESSION_CLEANUP_REGISTERED] = True


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


def resolve_worker_server() -> FastMCP | None:
    """Return the server owning the current task's tool, or None outside a task.

    Installed as core's worker-server resolver by ``TasksExtension`` so
    ``get_server()``/``CurrentFastMCP()`` inside a worker resolve to the (child)
    server the task was submitted against, not the root that runs the worker.
    The map is populated at submission (same process) and, for a remote worker,
    by ``make_task_context`` re-resolving from the snapshot before the tool runs.
    """
    task_info = get_task_context()
    if task_info is None:
        return None
    return get_task_server(task_info.task_id)


async def _resolve_owning_server(
    snapshot: TaskContextSnapshot | None,
) -> FastMCP | None:
    """Re-resolve a mounted task's owning child server from the root (remote worker).

    A separate worker process cannot reach the submitting process's server map,
    so the owning server is recovered by looking the snapshotted tool name up on
    the root: a mounted tool resolves to a ``FastMCPProviderTool`` referencing
    its child server. Returns ``None`` for an unmounted tool (the root owns it)
    or when the name no longer resolves, so the caller falls back to the root.
    """
    if snapshot is None or snapshot.owning_tool_name is None:
        return None
    from fastmcp.exceptions import NotFoundError
    from fastmcp.server.dependencies import get_server
    from fastmcp.server.providers.fastmcp_provider import FastMCPProviderTool
    from fastmcp.utilities.versions import VersionSpec

    root = get_server()
    # Resolve the exact version the call targeted: two versions of the same
    # mounted tool name can live on different child servers, so omitting the
    # version could pick the wrong server's state and masking policy.
    version = (
        VersionSpec(eq=snapshot.owning_tool_version)
        if snapshot.owning_tool_version
        else None
    )
    try:
        tool = await root.get_tool(snapshot.owning_tool_name, version)
    except NotFoundError:
        return None
    if isinstance(tool, FastMCPProviderTool):
        return tool._server
    return None


def _apply_snapshot_to_context(snapshot: TaskContextSnapshot) -> None:
    """Populate the ambient request context a worker's tool body reads.

    A Docket worker has no live request or SDK auth context — especially a
    Redis-backed worker in a separate process. This restores the context vars a
    tool reads so ``get_access_token()`` / ``get_http_headers()`` work unchanged:
    the SDK auth context var (from the snapshotted token) and core's background
    task-headers var (from the snapshotted headers). It deliberately does *not*
    fabricate a live ``Request``, so ``get_http_request()`` / ``CurrentRequest()``
    still raise inside a task — there is no request. Runs inside
    ``restore_task_snapshot`` (a Docket dependency), whose context vars propagate
    to the tool the same way the snapshot var already does.

    Both vars are set unconditionally to *this* snapshot's state (``None`` when
    it carries no token/headers), never left as-is: a Docket worker may reuse an
    asyncio context across tasks, so an anonymous task following an authenticated
    one must not inherit the prior caller's identity or headers.
    """
    import time

    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

    from fastmcp.server.auth import AccessToken
    from fastmcp.server.dependencies import (
        _background_task_headers,
        _background_task_session_id,
    )

    user: AuthenticatedUser | None = None
    if snapshot.access_token_json is not None:
        token = AccessToken.model_validate_json(snapshot.access_token_json)
        # A task may sit queued past its submitter's token expiry. Install it
        # only if still valid — mirroring the SDK's bearer check — so a delayed
        # task never runs under credentials a live request would reject (401).
        # An expired token leaves the worker unauthenticated, the honest state.
        if token.expires_at is None or token.expires_at >= int(time.time()):
            user = AuthenticatedUser(token)
    auth_context_var.set(user)

    _background_task_headers.set(
        dict(snapshot.http_headers) if snapshot.http_headers else None
    )
    _background_task_session_id.set(snapshot.session_id)


async def make_task_context() -> Context | None:
    """Build and enter a worker ``Context`` for the current background task.

    Installed as core's background-context factory by ``TasksExtension`` so a
    ``ctx: Context`` parameter resolves inside a Docket worker. Returns ``None``
    when not running in a task (so core falls through to its usual error). The
    snapshot restored by ``restore_task_snapshot`` supplies the origin request
    id; the server prefers the one registered at submission time so mounted
    tasks resolve to the child server. No live session is attached — SEP-2663
    input and status are polled, so the worker needs no back-channel.

    For a re-entered leg (after the client answered a guard ask), the accumulated
    per-leg state is loaded and injected so the tool reads ``ctx.input_responses``
    / ``ctx.request_state`` identically to the foreground guard contract. Leg 1
    loads nothing (both ``None``).
    """
    from fastmcp.server.context import Context
    from fastmcp.server.dependencies import get_server

    task_info = get_task_context()
    if task_info is None:
        return None

    snapshot = _recall_snapshot(task_info.task_id)
    server = get_task_server(task_info.task_id)
    if server is None:
        # In-process submission map missed — this is a remote worker (separate
        # process). Re-resolve the owning (child) server from the root using the
        # snapshotted tool name, and register it so `CurrentFastMCP()` mid-tool
        # resolves the child too. Falls back to the root when unmounted or
        # unresolvable.
        server = await _resolve_owning_server(snapshot) or get_server()
        register_task_server(task_info.task_id, server)
    origin_request_id = snapshot.origin_request_id if snapshot else None

    ctx = Context(
        fastmcp=server,
        session=None,
        task_id=task_info.task_id,
        origin_request_id=origin_request_id,
    )
    await ctx.__aenter__()

    docket = server._docket
    if docket is None:
        from fastmcp_tasks.dependencies import _current_docket

        docket = _current_docket.get()
    if docket is not None:
        from fastmcp_tasks.input_store import load_pending_input

        request_state, input_responses = await load_pending_input(
            docket, task_info.task_scope, task_info.task_id
        )
        ctx._task_request_state = request_state
        ctx._task_input_responses = input_responses

    return ctx
