"""Dependency injection for FastMCP.

DI features (Depends, CurrentContext, CurrentFastMCP) work without pydocket
using the uncalled-for DI engine. Only task-related dependencies (CurrentDocket,
CurrentWorker) and background task execution require fastmcp[tasks].
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import inspect
import json
import logging
import weakref
from collections import OrderedDict
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, cast, get_type_hints, runtime_checkable

from mcp.server.auth.middleware.auth_context import (
    get_access_token as _sdk_get_access_token,
)
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import (
    AccessToken as _SDKAccessToken,
)
from mcp.server.lowlevel.server import request_ctx
from packaging.version import Version
from starlette.requests import Request
from uncalled_for import Dependency, get_dependency_parameters
from uncalled_for.resolution import _Depends

from fastmcp.exceptions import FastMCPError
from fastmcp.server.auth import AccessToken
from fastmcp.server.http import _current_http_request
from fastmcp.utilities.async_utils import (
    call_sync_fn_in_threadpool,
    is_coroutine_function,
)
from fastmcp.utilities.types import find_kwarg_by_type, is_class_member_of_type

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from docket import Docket
    from docket.worker import Worker
    from mcp.server.session import ServerSession

    from fastmcp.server.context import Context
    from fastmcp.server.server import FastMCP


__all__ = [
    "AccessToken",
    "CurrentAccessToken",
    "CurrentContext",
    "CurrentDocket",
    "CurrentFastMCP",
    "CurrentHeaders",
    "CurrentRequest",
    "CurrentWorker",
    "Progress",
    "TaskContextInfo",
    "TaskContextSnapshot",
    "TokenClaim",
    "get_access_token",
    "get_context",
    "get_http_headers",
    "get_http_request",
    "get_server",
    "get_task_context",
    "get_task_session",
    "is_docket_available",
    "register_task_server",
    "register_task_session",
    "require_docket",
    "resolve_dependencies",
    "transform_context_annotations",
    "without_injected_parameters",
]


# --- TaskContextInfo and get_task_context ---


@dataclass(frozen=True, slots=True)
class TaskContextInfo:
    """Information about the current background task context.

    Returned by ``get_task_context()`` when running inside a Docket worker.
    Contains identifiers needed to communicate with the MCP session.
    """

    task_id: str
    """The MCP task ID (server-generated UUID)."""

    session_id: str
    """The session ID that submitted this task."""


def get_task_context() -> TaskContextInfo | None:
    """Get the current task context if running inside a background task worker.

    This function extracts task information from the Docket execution context.
    Returns None if not running in a task context (e.g., foreground execution).

    Returns:
        TaskContextInfo with task_id and session_id, or None if not in a task.
    """
    if not is_docket_available():
        return None

    from docket.dependencies import current_execution

    try:
        execution = current_execution.get()
        # Parse the task key: {session_id}:{task_id}:{task_type}:{component}
        from fastmcp.server.tasks.keys import parse_task_key

        key_parts = parse_task_key(execution.key)
        return TaskContextInfo(
            task_id=key_parts["client_task_id"],
            session_id=key_parts["session_id"],
        )
    except LookupError:
        # Not in worker context
        return None
    except (ValueError, KeyError):
        # Invalid task key format
        return None


# --- Session registry for background task Context ---


_task_sessions: dict[str, weakref.ref[ServerSession]] = {}


def register_task_session(session_id: str, session: ServerSession) -> None:
    """Register a session for Context access in background tasks.

    Called automatically when a task is submitted to Docket. The session is
    stored as a weakref so it doesn't prevent garbage collection when the
    client disconnects.

    Args:
        session_id: The session identifier
        session: The ServerSession instance
    """
    _task_sessions[session_id] = weakref.ref(session)


def get_task_session(session_id: str) -> ServerSession | None:
    """Get a registered session by ID if still alive.

    Args:
        session_id: The session identifier

    Returns:
        The ServerSession if found and alive, None otherwise
    """
    ref = _task_sessions.get(session_id)
    if ref is None:
        return None
    session = ref()
    if session is None:
        # Session was garbage collected, clean up entry
        _task_sessions.pop(session_id, None)
    return session


# --- ContextVars ---

_current_server: ContextVar[weakref.ref[FastMCP] | None] = ContextVar(
    "server", default=None
)

# --- Background task server map ---
# Maps task_id → server weakref so background workers can resolve the correct
# server for mounted-child tasks. Follows the same pattern as _task_sessions.
# Populated in submit_to_docket() where the child server is in context;
# consulted in get_server() when running inside a Docket worker.

_task_server_map: OrderedDict[str, weakref.ref[FastMCP]] = OrderedDict()
_TASK_SERVER_MAP_MAX_SIZE = 10_000


def register_task_server(task_id: str, server: FastMCP) -> None:
    """Register the server for a background task.

    Called at task-submission time (inside the child server's call_tool
    context) so that background workers can resolve CurrentFastMCP() and
    ctx.fastmcp to the child server for mounted tasks.

    The map is bounded to avoid unbounded growth in long-lived servers.
    Evicted entries fall back to the ContextVar (parent server).
    """
    _task_server_map[task_id] = weakref.ref(server)
    while len(_task_server_map) > _TASK_SERVER_MAP_MAX_SIZE:
        _task_server_map.popitem(last=False)


_current_docket: ContextVar[Docket | None] = ContextVar("docket", default=None)
_current_worker: ContextVar[Worker | None] = ContextVar("worker", default=None)


# --- Unified task context snapshot ---


@dataclass(frozen=True, slots=True)
class TaskContextSnapshot:
    """All context data snapshotted at task-submission time.

    Stored as a single Redis key per task, restored once in the worker.
    """

    access_token_json: str | None = None
    http_headers: dict[str, str] | None = None
    origin_request_id: str | None = None

    @classmethod
    def capture(cls) -> TaskContextSnapshot:
        """Capture current context for background task execution."""
        access_token = get_access_token()
        ctx = get_context()
        request_context = ctx.request_context
        return cls(
            access_token_json=(
                access_token.model_dump_json() if access_token else None
            ),
            http_headers=get_http_headers(include_all=True) or None,
            origin_request_id=(
                str(request_context.request_id) if request_context is not None else None
            ),
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
        )

    def to_json(self) -> str:
        """Serialize to JSON for Redis storage."""
        return json.dumps(
            {
                "access_token_json": self.access_token_json,
                "http_headers": self.http_headers,
                "origin_request_id": self.origin_request_id,
            }
        )

    async def save(
        self,
        docket: Docket,
        session_id: str,
        task_id: str,
        ttl_seconds: int,
    ) -> None:
        """Store this snapshot as a single Redis key."""
        key = docket.key(f"fastmcp:task:{session_id}:{task_id}:snapshot")
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


def _redis_key(session_id: str, task_id: str) -> str:
    """Build the Redis key suffix for a task snapshot."""
    return f"fastmcp:task:{session_id}:{task_id}:snapshot"


async def _load_task_snapshot_async(
    session_id: str, task_id: str
) -> TaskContextSnapshot | None:
    """Load task context snapshot from Redis (async) and cache it.

    Idempotent — returns the cached value if already loaded for this task.
    """
    cached = _get_cached_snapshot(task_id)
    if cached is not None:
        return cached

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
            raw = await redis.get(docket.key(_redis_key(session_id, task_id)))
        if raw is None:
            return None
        snapshot = TaskContextSnapshot.from_json(raw)
        _set_cached_snapshot(task_id, snapshot)
        return snapshot
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        _logger.warning(
            "Failed to load task snapshot for %s:%s",
            session_id,
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

    return _load_task_snapshot_sync(task_info.session_id, task_info.task_id)


def _load_task_snapshot_sync(
    session_id: str, task_id: str
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
        raw = sync_redis.get(docket.key(_redis_key(session_id, task_id)))
        if raw is None:
            return None
        snapshot = TaskContextSnapshot.from_json(raw)
        _set_cached_snapshot(task_id, snapshot)
        return snapshot
    except (OSError, json.JSONDecodeError, KeyError, ValueError, ImportError):
        _logger.warning(
            "Failed to load task snapshot via sync Redis for %s:%s",
            session_id,
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


# --- Docket availability check ---

_DOCKET_AVAILABLE: bool | None = None


_MIN_DOCKET_VERSION = Version("0.19.0")


def is_docket_available() -> bool:
    """Check if a compatible pydocket (>= 0.19.0) is installed and importable.

    Three things have to be true for fastmcp's task features to work:
      1. pydocket distribution metadata is discoverable
      2. its version is at least ``_MIN_DOCKET_VERSION`` (older versions are
         missing symbols like ``docket.dependencies.current_execution``,
         which fastmcp imports on the request hot path)
      3. the package actually imports — guards against broken/partial
         installs where metadata exists but ``import docket`` blows up

    Any of those failing means we treat docket as unavailable and fall back
    to the no-tasks code paths instead of crashing deep inside a request.
    """
    global _DOCKET_AVAILABLE
    if _DOCKET_AVAILABLE is None:
        try:
            installed = Version(importlib.metadata.version("pydocket"))
            if installed < _MIN_DOCKET_VERSION:
                _DOCKET_AVAILABLE = False
            else:
                import docket  # noqa: F401

                _DOCKET_AVAILABLE = True
        except (importlib.metadata.PackageNotFoundError, ImportError):
            _DOCKET_AVAILABLE = False
    return _DOCKET_AVAILABLE


def require_docket(feature: str) -> None:
    """Raise ImportError with install instructions if docket not available.

    Args:
        feature: Description of what requires docket (e.g., "`task=True`",
                 "CurrentDocket()"). Will be included in the error message.
    """
    if is_docket_available():
        return

    try:
        installed = importlib.metadata.version("pydocket")
    except importlib.metadata.PackageNotFoundError:
        installed = None

    if installed is None:
        detail = (
            "FastMCP background tasks require the `tasks` extra. "
            "Install with: pip install 'fastmcp[tasks]'."
        )
    else:
        detail = (
            f"FastMCP background tasks require pydocket>={_MIN_DOCKET_VERSION}, "
            f"but pydocket {installed} is installed (likely pulled in by another "
            f"package). Upgrade with: pip install -U 'pydocket>={_MIN_DOCKET_VERSION}'."
        )

    raise ImportError(f"{detail} (Triggered by {feature})")


# Import Progress separately — it's docket-specific, not part of uncalled-for
try:
    from docket.dependencies import Progress as DocketProgress
except ImportError:
    DocketProgress = None  # type: ignore[assignment]  # ty:ignore[invalid-assignment]


# --- Context utilities ---


def transform_context_annotations(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Transform ctx: Context into ctx: Context = CurrentContext().

    Transforms ALL params typed as Context to use Docket's DI system,
    unless they already have a Dependency-based default (like CurrentContext()).

    This unifies the legacy type annotation DI with Docket's Depends() system,
    allowing both patterns to work through a single resolution path.

    Note: Only POSITIONAL_OR_KEYWORD parameters are reordered (params with defaults
    after those without). KEYWORD_ONLY parameters keep their position since Python
    allows them to have defaults in any order.

    Args:
        fn: Function to transform

    Returns:
        Function with modified signature (same function object, updated __signature__)
    """
    from fastmcp.server.context import Context

    # Get the function's signature
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return fn

    # Get type hints for accurate type checking
    try:
        type_hints = get_type_hints(fn, include_extras=True)
    except Exception:
        type_hints = getattr(fn, "__annotations__", {})

    # First pass: identify which params need transformation
    params_to_transform: set[str] = set()
    optional_context_params: set[str] = set()
    for name, param in sig.parameters.items():
        annotation = type_hints.get(name, param.annotation)
        if is_class_member_of_type(annotation, Context):
            if not isinstance(param.default, Dependency):
                params_to_transform.add(name)
                if param.default is None:
                    optional_context_params.add(name)

    if not params_to_transform:
        return fn

    # Second pass: build new param list preserving parameter kind structure
    # Python signature structure: [POSITIONAL_ONLY] / [POSITIONAL_OR_KEYWORD] *args [KEYWORD_ONLY] **kwargs
    # Within POSITIONAL_ONLY and POSITIONAL_OR_KEYWORD: params without defaults must come first
    # KEYWORD_ONLY params can have defaults in any order
    P = inspect.Parameter

    # Group params by section, preserving order within each
    positional_only_no_default: list[P] = []
    positional_only_with_default: list[P] = []
    positional_or_keyword_no_default: list[P] = []
    positional_or_keyword_with_default: list[P] = []
    var_positional: list[P] = []  # *args (at most one)
    keyword_only: list[P] = []  # After * or *args, order preserved
    var_keyword: list[P] = []  # **kwargs (at most one)

    for name, param in sig.parameters.items():
        # Transform Context params by adding CurrentContext default
        if name in params_to_transform:
            # We use CurrentContext() instead of Depends(get_context) because
            # get_context() returns the Context which is an AsyncContextManager,
            # and the DI system would try to enter it again (it's already entered)
            if name in optional_context_params:
                param = param.replace(default=OptionalCurrentContext())
            else:
                param = param.replace(default=CurrentContext())

        # Sort into buckets based on parameter kind
        if param.kind == P.POSITIONAL_ONLY:
            if param.default is P.empty:
                positional_only_no_default.append(param)
            else:
                positional_only_with_default.append(param)
        elif param.kind == P.POSITIONAL_OR_KEYWORD:
            if param.default is P.empty:
                positional_or_keyword_no_default.append(param)
            else:
                positional_or_keyword_with_default.append(param)
        elif param.kind == P.VAR_POSITIONAL:
            var_positional.append(param)
        elif param.kind == P.KEYWORD_ONLY:
            keyword_only.append(param)
        elif param.kind == P.VAR_KEYWORD:
            var_keyword.append(param)

    # Reconstruct parameter list maintaining Python's required structure
    new_params: list[P] = (
        positional_only_no_default
        + positional_only_with_default
        + positional_or_keyword_no_default
        + positional_or_keyword_with_default
        + var_positional
        + keyword_only
        + var_keyword
    )

    # Update function's signature in place
    # Handle methods by setting signature on the underlying function
    # For bound methods, we need to preserve the 'self' parameter because
    # inspect.signature(bound_method) automatically removes the first param
    if inspect.ismethod(fn):
        # Get the original __func__ signature which includes 'self'
        func_sig = inspect.signature(fn.__func__)
        # Insert 'self' at the beginning of our new params
        self_param = next(iter(func_sig.parameters.values()))  # Should be 'self'
        new_sig = func_sig.replace(parameters=[self_param, *new_params])
        fn.__func__.__signature__ = new_sig  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
    else:
        new_sig = sig.replace(parameters=new_params)
        fn.__signature__ = new_sig  # type: ignore[attr-defined]  # ty:ignore[invalid-assignment]

    # Clear caches that may have cached the old signature
    # This ensures get_dependency_parameters and without_injected_parameters
    # see the transformed signature
    _clear_signature_caches(fn)

    return fn


def _clear_signature_caches(fn: Callable[..., Any]) -> None:
    """Clear signature-related caches for a function.

    Called after modifying a function's signature to ensure downstream
    code sees the updated signature.
    """
    from uncalled_for.introspection import _parameter_cache, _signature_cache

    _signature_cache.pop(fn, None)
    _parameter_cache.pop(fn, None)

    if inspect.ismethod(fn):
        _signature_cache.pop(fn.__func__, None)
        _parameter_cache.pop(fn.__func__, None)


def get_context() -> Context:
    """Get the current FastMCP Context instance directly."""
    from fastmcp.server.context import _current_context

    context = _current_context.get()
    if context is None:
        raise RuntimeError("No active context found.")
    return context


def get_server() -> FastMCP:
    """Get the current FastMCP server instance directly.

    In a background-task worker, checks the task-server map first so that
    mounted-child tasks resolve to the child server (not the parent that
    started the worker).

    Returns:
        The active FastMCP server

    Raises:
        RuntimeError: If no server in context
    """
    # In a task context, prefer the task-specific server mapping.
    # This handles mounted-child tasks where _current_server is the parent.
    task_info = get_task_context()
    if task_info is not None:
        ref = _task_server_map.get(task_info.task_id)
        if ref is not None:
            server = ref()
            if server is not None:
                return server
            # Server was garbage collected, clean up
            _task_server_map.pop(task_info.task_id, None)

    server_ref = _current_server.get()
    if server_ref is None:
        raise RuntimeError("No FastMCP server instance in context")
    server = server_ref()
    if server is None:
        raise RuntimeError("FastMCP server instance is no longer available")
    return server


def get_http_request() -> Request:
    """Get the current HTTP request.

    Tries MCP SDK's request_ctx first, then falls back to FastMCP's HTTP context.
    In background tasks, returns a synthetic request populated with the
    snapshotted headers from the originating HTTP request.
    """
    # Try MCP SDK's request_ctx first (set during normal MCP request handling)
    request = None
    with contextlib.suppress(LookupError):
        request = request_ctx.get().request

    # Fallback to FastMCP's HTTP context variable
    # This is needed during `on_initialize` middleware where request_ctx isn't set yet
    if request is None:
        request = _current_http_request.get()

    # In Docket workers, restore a minimal request from the snapshotted headers.
    # Uses sync fallback chain: ContextVar → in-memory dict → sync Redis.
    if request is None:
        snapshot = _get_task_snapshot_sync()
        task_headers = snapshot.http_headers if snapshot else None
        if task_headers:
            request = Request(
                {
                    "type": "http",
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": "/",
                    "raw_path": b"/",
                    "query_string": b"",
                    "headers": [
                        (name.encode("latin-1"), value.encode("latin-1"))
                        for name, value in task_headers.items()
                    ],
                    "client": None,
                    "server": None,
                    "root_path": "",
                }
            )

    if request is None:
        raise RuntimeError("No active HTTP request found.")
    return request


def get_http_headers(
    include_all: bool = False,
    include: set[str] | None = None,
) -> dict[str, str]:
    """Extract headers from the current HTTP request if available.

    Never raises an exception, even if there is no active HTTP request (in which case
    an empty dict is returned).

    By default, strips problematic headers like `content-length` and `authorization`
    that cause issues if forwarded to downstream services. If `include_all` is True,
    all headers are returned.

    The `include` parameter allows specific headers to be included even if they would
    normally be excluded. This is useful for proxy transports that need to forward
    authorization headers to upstream MCP servers.
    """
    if include_all:
        exclude_headers: set[str] = set()
    else:
        exclude_headers = {
            "host",
            "content-length",
            "content-type",
            "connection",
            "transfer-encoding",
            "upgrade",
            "te",
            "keep-alive",
            "expect",
            "accept",
            "authorization",
            # Proxy-related headers
            "proxy-authenticate",
            "proxy-authorization",
            "proxy-connection",
            # MCP-related headers
            "mcp-session-id",
        }
        if include:
            exclude_headers -= {h.lower() for h in include}
        # (just in case)
        if not all(h.lower() == h for h in exclude_headers):
            raise ValueError("Excluded headers must be lowercase")
    headers: dict[str, str] = {}

    try:
        request = get_http_request()
        for name, value in request.headers.items():
            lower_name = name.lower()
            if lower_name not in exclude_headers:
                headers[lower_name] = str(value)
        return headers
    except RuntimeError:
        return {}


def get_access_token() -> AccessToken | None:
    """Get the FastMCP access token from the current context.

    This function first tries to get the token from the current HTTP request's scope,
    which is more reliable for long-lived connections where the SDK's auth_context_var
    may become stale after token refresh. Falls back to the SDK's context var if no
    request is available. In background tasks (Docket workers), falls back to the
    token snapshot stored in Redis at task submission time.

    Returns:
        The access token if an authenticated user is available, None otherwise.
    """
    access_token: _SDKAccessToken | None = None

    # First, try to get from current HTTP request's scope (issue #1863)
    # This is more reliable than auth_context_var for Streamable HTTP sessions
    # where tokens may be refreshed between MCP messages
    try:
        request = get_http_request()
        user = request.scope.get("user")
        if isinstance(user, AuthenticatedUser):
            access_token = user.access_token
    except RuntimeError:
        # No HTTP request available, fall back to context var
        pass

    # Fall back to SDK's context var if we didn't get a token from the request
    if access_token is None:
        access_token = _sdk_get_access_token()

    # Fall back to background task snapshot (#3095)
    # In Docket workers, neither HTTP request nor SDK context var are available.
    # Uses sync fallback chain: ContextVar → in-memory dict → sync Redis.
    if access_token is None:
        snapshot = _get_task_snapshot_sync()
        if snapshot is not None and snapshot.access_token_json is not None:
            task_token = AccessToken.model_validate_json(snapshot.access_token_json)
            if task_token.expires_at is not None:
                if task_token.expires_at < int(datetime.now(timezone.utc).timestamp()):
                    return None
            return task_token

    if access_token is None or isinstance(access_token, AccessToken):
        return access_token

    # If the object is not a FastMCP AccessToken, convert it to one if the
    # fields are compatible (e.g. `claims` is not present in the SDK's AccessToken).
    # This is a workaround for the case where the SDK or auth provider returns a different type
    # If it fails, it will raise a TypeError
    try:
        access_token_as_dict = access_token.model_dump()
        return AccessToken(
            token=access_token_as_dict["token"],
            client_id=access_token_as_dict["client_id"],
            scopes=access_token_as_dict["scopes"],
            # Optional fields
            expires_at=access_token_as_dict.get("expires_at"),
            resource=access_token_as_dict.get("resource"),
            claims=access_token_as_dict.get("claims") or {},
        )
    except Exception as e:
        raise TypeError(
            f"Expected fastmcp.server.auth.auth.AccessToken, got {type(access_token).__name__}. "
            "Ensure the SDK is using the correct AccessToken type."
        ) from e


# --- Schema generation helper ---


@lru_cache(maxsize=5000)
def without_injected_parameters(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Create a wrapper function without injected parameters.

    Returns a wrapper that excludes Context and Docket dependency parameters,
    making it safe to use with Pydantic TypeAdapter for schema generation and
    validation. The wrapper internally handles all dependency resolution and
    Context injection when called.

    Handles:
    - Legacy Context injection (always works)
    - Depends() injection (always works - uses docket or vendored DI engine)

    Args:
        fn: Original function with Context and/or dependencies

    Returns:
        Async wrapper function without injected parameters
    """
    from fastmcp.server.context import Context

    # Identify parameters to exclude
    context_kwarg = find_kwarg_by_type(fn, Context)
    dependency_params = get_dependency_parameters(fn)

    exclude = set()
    if context_kwarg:
        exclude.add(context_kwarg)
    if dependency_params:
        exclude.update(dependency_params.keys())

    if not exclude:
        return fn

    # Build new signature with only user parameters
    sig = inspect.signature(fn)
    user_params = [
        param for name, param in sig.parameters.items() if name not in exclude
    ]
    new_sig = inspect.Signature(user_params)

    # Create async wrapper that handles dependency resolution
    fn_is_async = is_coroutine_function(fn)

    async def wrapper(**user_kwargs: Any) -> Any:
        async with resolve_dependencies(fn, user_kwargs) as resolved_kwargs:
            if fn_is_async:
                return await fn(**resolved_kwargs)
            else:
                # Run sync functions in threadpool to avoid blocking the event loop
                result = await call_sync_fn_in_threadpool(fn, **resolved_kwargs)
                # Handle sync wrappers that return awaitables (e.g., partial(async_fn))
                if inspect.isawaitable(result):
                    result = await result
                return result

    # Resolve string annotations (from `from __future__ import annotations`) using
    # the original function's module context. The wrapper's __globals__ points to
    # this module (dependencies.py) and is read-only, so some Pydantic versions
    # can't resolve names like Annotated or Literal from string annotations.
    try:
        resolved_hints = get_type_hints(fn, include_extras=True)
    except Exception:
        resolved_hints = getattr(fn, "__annotations__", {})

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
    wrapper.__annotations__ = {
        k: v for k, v in resolved_hints.items() if k not in exclude and k != "return"
    }
    wrapper.__name__ = getattr(fn, "__name__", "wrapper")
    wrapper.__doc__ = getattr(fn, "__doc__", None)
    wrapper.__module__ = fn.__module__
    wrapper.__qualname__ = getattr(fn, "__qualname__", wrapper.__qualname__)

    return wrapper


# --- Dependency resolution ---


@asynccontextmanager
async def _resolve_fastmcp_dependencies(
    fn: Callable[..., Any], arguments: dict[str, Any]
) -> AsyncGenerator[dict[str, Any], None]:
    """Resolve Docket dependencies for a FastMCP function.

    Sets up the minimal context needed for Docket's Depends() to work:
    - A cache for resolved dependencies
    - An AsyncExitStack for managing context manager lifetimes

    The Docket instance (for CurrentDocket dependency) is managed separately
    by the server's lifespan and made available via ContextVar.

    Note: This does NOT set up Docket's Execution context. If user code needs
    Docket-specific dependencies like TaskArgument(), TaskKey(), etc., those
    will fail with clear errors about missing context.

    Args:
        fn: The function to resolve dependencies for
        arguments: The arguments passed to the function

    Yields:
        Dictionary of resolved dependencies merged with provided arguments
    """
    dependency_params = get_dependency_parameters(fn)

    if not dependency_params:
        yield arguments
        return

    # Initialize dependency cache and exit stack
    cache_token = _Depends.cache.set({})
    try:
        async with AsyncExitStack() as stack:
            stack_token = _Depends.stack.set(stack)
            try:
                resolved: dict[str, Any] = {}

                for parameter, dependency in dependency_params.items():
                    # If argument was explicitly provided, use that instead
                    if parameter in arguments:
                        resolved[parameter] = arguments[parameter]
                        continue

                    # Resolve the dependency
                    try:
                        resolved[parameter] = await stack.enter_async_context(
                            dependency
                        )
                    except FastMCPError:
                        # Let FastMCPError subclasses (ToolError, ResourceError, etc.)
                        # propagate unchanged so they can be handled appropriately
                        raise
                    except Exception as error:
                        fn_name = getattr(fn, "__name__", repr(fn))
                        raise RuntimeError(
                            f"Failed to resolve dependency '{parameter}' for {fn_name}"
                        ) from error

                # Merge resolved dependencies with provided arguments
                final_arguments = {**arguments, **resolved}

                yield final_arguments
            finally:
                _Depends.stack.reset(stack_token)
    finally:
        _Depends.cache.reset(cache_token)


@asynccontextmanager
async def resolve_dependencies(
    fn: Callable[..., Any], arguments: dict[str, Any]
) -> AsyncGenerator[dict[str, Any], None]:
    """Resolve dependencies for a FastMCP function.

    This function:
    1. Filters out any dependency parameter names from user arguments (security)
    2. Resolves Depends() parameters via the DI system

    The filtering prevents external callers from overriding injected parameters by
    providing values for dependency parameter names. This is a security feature.

    Note: Context injection is handled via transform_context_annotations() which
    converts `ctx: Context` to `ctx: Context = Depends(get_context)` at registration
    time, so all injection goes through the unified DI system.

    Args:
        fn: The function to resolve dependencies for
        arguments: User arguments (may contain keys that match dependency names,
                  which will be filtered out)

    Yields:
        Dictionary of filtered user args + resolved dependencies

    Example:
        ```python
        async with resolve_dependencies(my_tool, {"name": "Alice"}) as kwargs:
            result = my_tool(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        ```
    """
    # Filter out dependency parameters from user arguments to prevent override
    # This is a security measure - external callers should never be able to
    # provide values for injected parameters
    dependency_params = get_dependency_parameters(fn)
    user_args = {k: v for k, v in arguments.items() if k not in dependency_params}

    async with _resolve_fastmcp_dependencies(fn, user_args) as resolved_kwargs:
        yield resolved_kwargs


# --- Dependency classes ---
# These must inherit from docket.dependencies.Dependency when docket is available
# so that get_dependency_parameters can detect them.


class _CurrentContext(Dependency["Context"]):
    """Async context manager for Context dependency.

    In foreground (request) mode: returns the active context from _current_context.
    In background (Docket worker) mode: creates a task-aware Context with task_id
    and loads the unified task snapshot from Redis.

    The shared default instance is a stateless factory. All per-invocation
    state lives on the returned Context or in task-local ContextVars, so
    concurrent tasks never share mutable state.
    """

    async def __aenter__(self) -> Context:
        from fastmcp.server.context import Context, _current_context

        # Try foreground context first (normal MCP request)
        context = _current_context.get()
        if context is not None:
            return context

        # Check if we're in a Docket worker context
        task_info = get_task_context()
        if task_info is not None:
            session = get_task_session(task_info.session_id)
            server = get_server()

            # Load unified snapshot (sets _task_snapshot ContextVar)
            snapshot = await _load_task_snapshot_async(
                task_info.session_id, task_info.task_id
            )
            origin_request_id = snapshot.origin_request_id if snapshot else None

            ctx = Context(
                fastmcp=server,
                session=session,
                task_id=task_info.task_id,
                origin_request_id=origin_request_id,
            )
            await ctx.__aenter__()
            return ctx

        raise RuntimeError(
            "No active context found. This can happen if:\n"
            "  - Called outside an MCP request handler\n"
            "  - Called in a background task before session was registered\n"
            "Check `context.request_context` for None before accessing."
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        from fastmcp.server.context import _current_context

        ctx = _current_context.get()
        if ctx is not None and ctx.is_background_task:
            await ctx.__aexit__(exc_type, exc_value, traceback)


class _OptionalCurrentContext(Dependency["Context | None"]):
    """Context dependency that returns None instead of raising when no context
    is active. Used for ``ctx: Context = None`` parameter patterns.

    Delegates entirely to ``_CurrentContext`` — just catches the RuntimeError.
    Cleanup is handled by ``_CurrentContext.__aexit__`` reading from the
    task-local ContextVar.
    """

    async def __aenter__(self) -> Context | None:
        try:
            return await _CurrentContext().__aenter__()
        except RuntimeError as exc:
            if "No active context found" in str(exc):
                return None
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        from fastmcp.server.context import _current_context

        ctx = _current_context.get()
        if ctx is not None and ctx.is_background_task:
            await _CurrentContext().__aexit__(exc_type, exc_value, traceback)


def CurrentContext() -> Context:
    """Get the current FastMCP Context instance.

    This dependency provides access to the active FastMCP Context for the
    current MCP operation (tool/resource/prompt call).

    Returns:
        A dependency that resolves to the active Context instance

    Raises:
        RuntimeError: If no active context found (during resolution)

    Example:
        ```python
        from fastmcp.dependencies import CurrentContext

        @mcp.tool()
        async def log_progress(ctx: Context = CurrentContext()) -> str:
            ctx.report_progress(50, 100, "Halfway done")
            return "Working"
        ```
    """
    return cast("Context", _CurrentContext())


def OptionalCurrentContext() -> Context | None:
    """Get the current FastMCP Context, or None when no context is active."""
    return cast("Context | None", _OptionalCurrentContext())


class _CurrentDocket(Dependency["Docket"]):
    """Async context manager for Docket dependency."""

    async def __aenter__(self) -> Docket:
        require_docket("CurrentDocket()")
        # Check server instance first, fall back to ContextVar for mounted children
        # whose parent owns the Docket
        try:
            docket = get_server()._docket
        except RuntimeError:
            docket = None
        if docket is None:
            docket = _current_docket.get()
        if docket is None:
            raise RuntimeError(
                "No Docket instance found. Docket is only initialized when there are "
                "task-enabled components (task=True). Add task=True to a component "
                "to enable Docket infrastructure."
            )
        return docket

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentDocket() -> Docket:
    """Get the current Docket instance managed by FastMCP.

    This dependency provides access to the Docket instance that FastMCP
    automatically creates for background task scheduling.

    Returns:
        A dependency that resolves to the active Docket instance

    Raises:
        RuntimeError: If not within a FastMCP server context
        ImportError: If fastmcp[tasks] not installed

    Example:
        ```python
        from fastmcp.dependencies import CurrentDocket

        @mcp.tool()
        async def schedule_task(docket: Docket = CurrentDocket()) -> str:
            await docket.add(some_function)(arg1, arg2)
            return "Scheduled"
        ```
    """
    require_docket("CurrentDocket()")
    return cast("Docket", _CurrentDocket())


class _CurrentWorker(Dependency["Worker"]):
    """Async context manager for Worker dependency."""

    async def __aenter__(self) -> Worker:
        require_docket("CurrentWorker()")
        # Check server instance first, fall back to ContextVar for mounted children
        try:
            worker = get_server()._worker
        except RuntimeError:
            worker = None
        if worker is None:
            worker = _current_worker.get()
        if worker is None:
            raise RuntimeError(
                "No Worker instance found. Worker is only initialized when there are "
                "task-enabled components (task=True). Add task=True to a component "
                "to enable Docket infrastructure."
            )
        return worker

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentWorker() -> Worker:
    """Get the current Docket Worker instance managed by FastMCP.

    This dependency provides access to the Worker instance that FastMCP
    automatically creates for background task processing.

    Returns:
        A dependency that resolves to the active Worker instance

    Raises:
        RuntimeError: If not within a FastMCP server context
        ImportError: If fastmcp[tasks] not installed

    Example:
        ```python
        from fastmcp.dependencies import CurrentWorker

        @mcp.tool()
        async def check_worker_status(worker: Worker = CurrentWorker()) -> str:
            return f"Worker: {worker.name}"
        ```
    """
    require_docket("CurrentWorker()")
    return cast("Worker", _CurrentWorker())


class _CurrentFastMCP(Dependency["FastMCP"]):
    """Async context manager for FastMCP server dependency."""

    async def __aenter__(self) -> FastMCP:
        return get_server()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentFastMCP() -> FastMCP:
    """Get the current FastMCP server instance.

    This dependency provides access to the active FastMCP server.

    Returns:
        A dependency that resolves to the active FastMCP server

    Raises:
        RuntimeError: If no server in context (during resolution)

    Example:
        ```python
        from fastmcp.dependencies import CurrentFastMCP

        @mcp.tool()
        async def introspect(server: FastMCP = CurrentFastMCP()) -> str:
            return f"Server: {server.name}"
        ```
    """
    from fastmcp.server.server import FastMCP

    return cast(FastMCP, _CurrentFastMCP())


class _CurrentRequest(Dependency[Request]):
    """Async context manager for HTTP Request dependency."""

    async def __aenter__(self) -> Request:
        return get_http_request()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentRequest() -> Request:
    """Get the current HTTP request.

    This dependency provides access to the Starlette Request object for the
    current HTTP request. Only available when running over HTTP transports
    (SSE or Streamable HTTP).

    Returns:
        A dependency that resolves to the active Starlette Request

    Raises:
        RuntimeError: If no HTTP request in context (e.g., STDIO transport)

    Example:
        ```python
        from fastmcp.server.dependencies import CurrentRequest
        from starlette.requests import Request

        @mcp.tool()
        async def get_client_ip(request: Request = CurrentRequest()) -> str:
            return request.client.host if request.client else "Unknown"
        ```
    """
    return cast(Request, _CurrentRequest())


class _CurrentHeaders(Dependency[dict[str, str]]):
    """Async context manager for HTTP Headers dependency."""

    async def __aenter__(self) -> dict[str, str]:
        return get_http_headers(include={"authorization"})

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentHeaders() -> dict[str, str]:
    """Get the current HTTP request headers.

    This dependency provides access to the HTTP headers for the current request,
    including the authorization header. Returns an empty dictionary when no HTTP
    request is available, making it safe to use in code that might run over any
    transport.

    Returns:
        A dependency that resolves to a dictionary of header name -> value

    Example:
        ```python
        from fastmcp.server.dependencies import CurrentHeaders

        @mcp.tool()
        async def get_auth_type(headers: dict = CurrentHeaders()) -> str:
            auth = headers.get("authorization", "")
            return "Bearer" if auth.startswith("Bearer ") else "None"
        ```
    """
    return cast(dict[str, str], _CurrentHeaders())


# --- Progress dependency ---


@runtime_checkable
class ProgressLike(Protocol):
    """Protocol for progress tracking interface.

    Defines the common interface between InMemoryProgress (server context)
    and Docket's Progress (worker context).
    """

    @property
    def current(self) -> int | None:
        """Current progress value."""
        ...

    @property
    def total(self) -> int:
        """Total/target progress value."""
        ...

    @property
    def message(self) -> str | None:
        """Current progress message."""
        ...

    async def set_total(self, total: int) -> None:
        """Set the total/target value for progress tracking."""
        ...

    async def increment(self, amount: int = 1) -> None:
        """Atomically increment the current progress value."""
        ...

    async def set_message(self, message: str | None) -> None:
        """Update the progress status message."""
        ...


class InMemoryProgress:
    """In-memory progress tracker for immediate tool execution.

    Provides the same interface as Docket's Progress but stores state in memory
    instead of Redis. Useful for testing and immediate execution where
    progress doesn't need to be observable across processes.
    """

    def __init__(self) -> None:
        self._current: int | None = None
        self._total: int = 1
        self._message: str | None = None

    async def __aenter__(self) -> InMemoryProgress:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    @property
    def current(self) -> int | None:
        return self._current

    @property
    def total(self) -> int:
        return self._total

    @property
    def message(self) -> str | None:
        return self._message

    async def set_total(self, total: int) -> None:
        """Set the total/target value for progress tracking."""
        if total < 1:
            raise ValueError("Total must be at least 1")
        self._total = total

    async def increment(self, amount: int = 1) -> None:
        """Atomically increment the current progress value."""
        if amount < 1:
            raise ValueError("Amount must be at least 1")
        if self._current is None:
            self._current = amount
        else:
            self._current += amount

    async def set_message(self, message: str | None) -> None:
        """Update the progress status message."""
        self._message = message


class Progress(Dependency["Progress"]):
    """Progress dependency that works in both server and worker contexts.

    In a Docket worker, delegates to the execution's Redis-backed progress
    (observable across processes). Otherwise, uses in-memory tracking.

    The shared default instance acts as a stateless factory — ``__aenter__``
    creates a fresh ``Progress`` per invocation so concurrent tasks never
    share mutable state.
    """

    _impl: ProgressLike | None = None

    async def __aenter__(self) -> Progress:
        server_ref = _current_server.get()
        if server_ref is None or server_ref() is None:
            raise RuntimeError("Progress dependency requires a FastMCP server context.")

        instance = Progress()

        if is_docket_available():
            try:
                from docket.dependencies import current_execution

                instance._impl = current_execution.get().progress
                return instance
            except LookupError:
                pass

        instance._impl = InMemoryProgress()
        return instance

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass

    @property
    def current(self) -> int | None:
        """Current progress value."""
        assert self._impl is not None, "Progress must be used as a dependency"
        return self._impl.current

    @property
    def total(self) -> int:
        """Total/target progress value."""
        assert self._impl is not None, "Progress must be used as a dependency"
        return self._impl.total

    @property
    def message(self) -> str | None:
        """Current progress message."""
        assert self._impl is not None, "Progress must be used as a dependency"
        return self._impl.message

    async def set_total(self, total: int) -> None:
        """Set the total/target value for progress tracking."""
        assert self._impl is not None, "Progress must be used as a dependency"
        await self._impl.set_total(total)

    async def increment(self, amount: int = 1) -> None:
        """Atomically increment the current progress value."""
        assert self._impl is not None, "Progress must be used as a dependency"
        await self._impl.increment(amount)

    async def set_message(self, message: str | None) -> None:
        """Update the progress status message."""
        assert self._impl is not None, "Progress must be used as a dependency"
        await self._impl.set_message(message)


# --- Access Token dependency ---


class _CurrentAccessToken(Dependency[AccessToken]):
    """Async context manager for AccessToken dependency."""

    async def __aenter__(self) -> AccessToken:
        token = get_access_token()

        if token is None:
            raise RuntimeError(
                "No access token found. Ensure authentication is configured "
                "and the request is authenticated."
            )
        return token

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def CurrentAccessToken() -> AccessToken:
    """Get the current access token for the authenticated user.

    This dependency provides access to the AccessToken for the current
    authenticated request. Raises an error if no authentication is present.

    Returns:
        A dependency that resolves to the active AccessToken

    Raises:
        RuntimeError: If no authenticated user (use get_access_token() for optional)

    Example:
        ```python
        from fastmcp.server.dependencies import CurrentAccessToken
        from fastmcp.server.auth import AccessToken

        @mcp.tool()
        async def get_user_id(token: AccessToken = CurrentAccessToken()) -> str:
            return token.claims.get("sub", "unknown")
        ```
    """
    return cast(AccessToken, _CurrentAccessToken())


# --- Token Claim dependency ---


class _TokenClaim(Dependency[str]):
    """Dependency that extracts a specific claim from the access token."""

    def __init__(self, claim_name: str):
        self.claim_name = claim_name

    async def __aenter__(self) -> str:
        token = get_access_token()
        if token is None:
            raise RuntimeError(
                f"No access token available. Cannot extract claim '{self.claim_name}'."
            )
        value = token.claims.get(self.claim_name)
        if value is None:
            raise RuntimeError(
                f"Claim '{self.claim_name}' not found in access token. "
                f"Available claims: {list(token.claims.keys())}"
            )
        return str(value)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


def TokenClaim(name: str) -> str:
    """Get a specific claim from the access token.

    This dependency extracts a single claim value from the current access token.
    It's useful for getting user identifiers, roles, or other token claims
    without needing the full token object.

    Args:
        name: The name of the claim to extract (e.g., "oid", "sub", "email")

    Returns:
        A dependency that resolves to the claim value as a string

    Raises:
        RuntimeError: If no access token is available or claim is missing

    Example:
        ```python
        from fastmcp.server.dependencies import TokenClaim

        @mcp.tool()
        async def add_expense(
            user_id: str = TokenClaim("oid"),  # Azure object ID
            amount: float,
        ):
            # user_id is automatically injected from the token
            await db.insert({"user_id": user_id, "amount": amount})
        ```
    """
    return cast(str, _TokenClaim(name))
