from __future__ import annotations

import contextlib
import inspect
import weakref
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast, get_type_hints

from docket.dependencies import Dependency, _Depends, get_dependency_parameters
from docket.dependencies import Progress as DocketProgress
from mcp.server.auth.middleware.auth_context import (
    get_access_token as _sdk_get_access_token,
)
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import (
    AccessToken as _SDKAccessToken,
)
from mcp.server.lowlevel.server import request_ctx
from starlette.requests import Request

from fastmcp.exceptions import FastMCPError
from fastmcp.server.auth import AccessToken
from fastmcp.server.http import _current_http_request
from fastmcp.utilities.types import is_class_member_of_type

if TYPE_CHECKING:
    from docket import Docket
    from docket.worker import Worker

    from fastmcp.server.context import Context
    from fastmcp.server.server import FastMCP

# ContextVars for tracking Docket infrastructure
_current_docket: ContextVar[Docket | None] = ContextVar("docket", default=None)  # type: ignore[assignment]
_current_worker: ContextVar[Worker | None] = ContextVar("worker", default=None)  # type: ignore[assignment]
_current_server: ContextVar[weakref.ref[FastMCP] | None] = ContextVar(  # type: ignore[invalid-assignment]
    "server", default=None
)

__all__ = [
    "AccessToken",
    "CurrentContext",
    "CurrentDocket",
    "CurrentFastMCP",
    "CurrentWorker",
    "Progress",
    "get_access_token",
    "get_context",
    "get_http_headers",
    "get_http_request",
    "get_server",
    "resolve_dependencies",
    "without_injected_parameters",
]


def _find_kwarg_by_type(fn: Callable, kwarg_type: type) -> str | None:
    """Find the name of the kwarg that is of type kwarg_type.

    This is the legacy dependency injection approach, used specifically for
    injecting the Context object when a function parameter is typed as Context.

    Includes union types that contain the kwarg_type, as well as Annotated types.
    """

    if inspect.ismethod(fn) and hasattr(fn, "__func__"):
        fn = fn.__func__

    try:
        type_hints = get_type_hints(fn, include_extras=True)
    except Exception:
        type_hints = getattr(fn, "__annotations__", {})

    sig = inspect.signature(fn)
    for name, param in sig.parameters.items():
        annotation = type_hints.get(name, param.annotation)
        if is_class_member_of_type(annotation, kwarg_type):
            return name
    return None


@lru_cache(maxsize=5000)
def without_injected_parameters(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Create a wrapper function without injected parameters.

    Returns a wrapper that excludes Context and Docket dependency parameters,
    making it safe to use with Pydantic TypeAdapter for schema generation and
    validation. The wrapper internally handles all dependency resolution and
    Context injection when called.

    Args:
        fn: Original function with Context and/or dependencies

    Returns:
        Async wrapper function without injected parameters
    """
    from fastmcp.server.context import Context

    # Identify parameters to exclude
    context_kwarg = _find_kwarg_by_type(fn, Context)
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
    async def wrapper(**user_kwargs: Any) -> Any:
        async with resolve_dependencies(fn, user_kwargs) as resolved_kwargs:
            result = fn(**resolved_kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

    # Set wrapper metadata (only parameter annotations, not return type)
    wrapper.__signature__ = new_sig  # type: ignore
    wrapper.__annotations__ = {
        k: v
        for k, v in getattr(fn, "__annotations__", {}).items()
        if k not in exclude and k != "return"
    }
    wrapper.__name__ = getattr(fn, "__name__", "wrapper")
    wrapper.__doc__ = getattr(fn, "__doc__", None)

    return wrapper


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
    """Resolve dependencies and inject Context for a FastMCP function.

    This function:
    1. Filters out any dependency parameter names from user arguments (security)
    2. Resolves Docket dependencies
    3. Injects Context if needed
    4. Merges everything together

    The filtering prevents external callers from overriding injected parameters by
    providing values for dependency parameter names. This is a security feature.

    Args:
        fn: The function to resolve dependencies for
        arguments: User arguments (may contain keys that match dependency names,
                  which will be filtered out)

    Yields:
        Dictionary of filtered user args + resolved dependencies + Context

    Example:
        ```python
        async with resolve_dependencies(my_tool, {"name": "Alice"}) as kwargs:
            result = my_tool(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        ```
    """
    from fastmcp.server.context import Context

    # Filter out dependency parameters from user arguments to prevent override
    # This is a security measure - external callers should never be able to
    # provide values for injected parameters
    dependency_params = get_dependency_parameters(fn)
    user_args = {k: v for k, v in arguments.items() if k not in dependency_params}

    async with _resolve_fastmcp_dependencies(fn, user_args) as resolved_kwargs:
        # Inject Context if needed
        context_kwarg = _find_kwarg_by_type(fn, kwarg_type=Context)
        if context_kwarg and context_kwarg not in resolved_kwargs:
            resolved_kwargs[context_kwarg] = get_context()

        yield resolved_kwargs


def get_context() -> Context:
    from fastmcp.server.context import _current_context

    context = _current_context.get()
    if context is None:
        raise RuntimeError("No active context found.")
    return context


class _CurrentContext(Dependency):
    """Internal dependency class for CurrentContext."""

    async def __aenter__(self) -> Context:
        return get_context()


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


class _CurrentDocket(Dependency):
    """Internal dependency class for CurrentDocket."""

    async def __aenter__(self) -> Docket:
        # Get Docket from ContextVar (set by _docket_lifespan)
        docket = _current_docket.get()
        if docket is None:
            raise RuntimeError(
                "No Docket instance found. Docket is only available within "
                "a running FastMCP server context."
            )

        return docket


def CurrentDocket() -> Docket:
    """Get the current Docket instance managed by FastMCP.

    This dependency provides access to the Docket instance that FastMCP
    automatically creates for background task scheduling.

    Returns:
        A dependency that resolves to the active Docket instance

    Raises:
        RuntimeError: If not within a FastMCP server context

    Example:
        ```python
        from fastmcp.dependencies import CurrentDocket

        @mcp.tool()
        async def schedule_task(docket: Docket = CurrentDocket()) -> str:
            await docket.add(some_function)(arg1, arg2)
            return "Scheduled"
        ```
    """
    return cast("Docket", _CurrentDocket())


class _CurrentWorker(Dependency):
    """Internal dependency class for CurrentWorker."""

    async def __aenter__(self) -> Worker:
        worker = _current_worker.get()
        if worker is None:
            raise RuntimeError(
                "No Worker instance found. Worker is only available within "
                "a running FastMCP server context."
            )

        return worker


def CurrentWorker() -> Worker:
    """Get the current Docket Worker instance managed by FastMCP.

    This dependency provides access to the Worker instance that FastMCP
    automatically creates for background task processing.

    Returns:
        A dependency that resolves to the active Worker instance

    Raises:
        RuntimeError: If not within a FastMCP server context

    Example:
        ```python
        from fastmcp.dependencies import CurrentWorker

        @mcp.tool()
        async def check_worker_status(worker: Worker = CurrentWorker()) -> str:
            return f"Worker: {worker.name}"
        ```
    """
    return cast("Worker", _CurrentWorker())


class InMemoryProgress(DocketProgress):
    """In-memory progress tracker for immediate tool execution.

    Provides the same interface as Progress but stores state in memory
    instead of Redis. Useful for testing and immediate execution where
    progress doesn't need to be observable across processes.
    """

    def __init__(self) -> None:
        super().__init__()
        self._current: int | None = None
        self._total: int = 1
        self._message: str | None = None

    async def __aenter__(self) -> DocketProgress:
        return self

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


class Progress(DocketProgress):
    """FastMCP Progress dependency that works in both server and worker contexts.

    Extends Docket's Progress to handle two execution modes:
    - In Docket worker: Uses the execution's progress (standard Docket behavior)
    - In FastMCP server: Uses in-memory progress (not observable remotely)

    This allows tools to use Progress() regardless of whether they're called
    immediately or as background tasks.
    """

    async def __aenter__(self) -> DocketProgress:
        # Try to get execution from Docket worker context
        try:
            return await super().__aenter__()
        except LookupError:
            # Not in worker context - return in-memory progress
            docket = _current_docket.get()
            if docket is None:
                raise RuntimeError(
                    "Progress dependency requires a FastMCP server context."
                ) from None

            # Return in-memory progress for immediate execution
            return InMemoryProgress()


class _CurrentFastMCP(Dependency):
    """Internal dependency class for CurrentFastMCP."""

    async def __aenter__(self):
        server_ref = _current_server.get()
        if server_ref is None:
            raise RuntimeError("No FastMCP server instance in context")
        server = server_ref()
        if server is None:
            raise RuntimeError("FastMCP server instance is no longer available")
        return server


def CurrentFastMCP():
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


def get_server():
    """Get the current FastMCP server instance directly.

    Returns:
        The active FastMCP server

    Raises:
        RuntimeError: If no server in context
    """

    server_ref = _current_server.get()
    if server_ref is None:
        raise RuntimeError("No FastMCP server instance in context")
    server = server_ref()
    if server is None:
        raise RuntimeError("FastMCP server instance is no longer available")
    return server


def get_http_request() -> Request:
    # Try MCP SDK's request_ctx first (set during normal MCP request handling)
    request = None
    with contextlib.suppress(LookupError):
        request = request_ctx.get().request

    # Fallback to FastMCP's HTTP context variable
    # This is needed during `on_initialize` middleware where request_ctx isn't set yet
    if request is None:
        request = _current_http_request.get()

    if request is None:
        raise RuntimeError("No active HTTP request found.")
    return request


def get_http_headers(include_all: bool = False) -> dict[str, str]:
    """
    Extract headers from the current HTTP request if available.

    Never raises an exception, even if there is no active HTTP request (in which case
    an empty dict is returned).

    By default, strips problematic headers like `content-length` that cause issues if forwarded to downstream clients.
    If `include_all` is True, all headers are returned.
    """
    if include_all:
        exclude_headers = set()
    else:
        exclude_headers = {
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            "upgrade",
            "te",
            "keep-alive",
            "expect",
            "accept",
            # Proxy-related headers
            "proxy-authenticate",
            "proxy-authorization",
            "proxy-connection",
            # MCP-related headers
            "mcp-session-id",
        }
        # (just in case)
        if not all(h.lower() == h for h in exclude_headers):
            raise ValueError("Excluded headers must be lowercase")
    headers = {}

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
    """
    Get the FastMCP access token from the current context.

    This function first tries to get the token from the current HTTP request's scope,
    which is more reliable for long-lived connections where the SDK's auth_context_var
    may become stale after token refresh. Falls back to the SDK's context var if no
    request is available.

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
            resource_owner=access_token_as_dict.get("resource_owner"),
            claims=access_token_as_dict.get("claims"),
        )
    except Exception as e:
        raise TypeError(
            f"Expected fastmcp.server.auth.auth.AccessToken, got {type(access_token).__name__}. "
            "Ensure the SDK is using the correct AccessToken type."
        ) from e
