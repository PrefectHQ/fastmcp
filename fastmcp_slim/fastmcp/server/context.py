from __future__ import annotations

import logging
import warnings
import weakref
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from logging import Logger
from typing import Any, Literal, cast, overload

import mcp_types
from mcp import LoggingLevel, ServerSession
from mcp.server.context import ServerRequestContext
from mcp_types import (
    GetPromptResult,
    ModelPreferences,
    Root,
    SamplingMessage,
)
from mcp_types import Prompt as SDKPrompt
from mcp_types import Resource as SDKResource
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic.networks import AnyUrl
from typing_extensions import TypeVar
from uncalled_for import SharedContext

import fastmcp
from fastmcp.exceptions import FastMCPDeprecationWarning, ToolError
from fastmcp.resources.base import ResourceResult
from fastmcp.server.dependencies import FastMCPRequestContext, fastmcp_request_ctx
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    handle_elicit_accept,
    parse_elicit_response_type,
)
from fastmcp.server.low_level import client_supports_extension
from fastmcp.server.sampling import SampleStep, SamplingResult, SamplingTool
from fastmcp.server.sampling.run import (
    sample_impl,
    sample_step_impl,
)
from fastmcp.server.server import FastMCP, StateValue
from fastmcp.server.transforms.visibility import (
    Visibility,
)
from fastmcp.server.transforms.visibility import (
    disable_components as _disable_components,
)
from fastmcp.server.transforms.visibility import (
    enable_components as _enable_components,
)
from fastmcp.server.transforms.visibility import (
    get_session_transforms as _get_session_transforms,
)
from fastmcp.server.transforms.visibility import (
    get_visibility_rules as _get_visibility_rules,
)
from fastmcp.server.transforms.visibility import (
    reset_visibility as _reset_visibility,
)
from fastmcp.utilities.logging import _clamp_logger, get_logger
from fastmcp.utilities.versions import VersionSpec

logger: Logger = get_logger(name=__name__)
to_client_logger: Logger = logger.getChild(suffix="to_client")

# Convert all levels of server -> client messages to debug level
# This clamp can be undone at runtime by calling `_unclamp_logger` or calling
# `_clamp_logger` with a different max level.
_clamp_logger(logger=to_client_logger, max_level="DEBUG")


T = TypeVar("T", default=Any)
ResultT = TypeVar("ResultT", default=str)

# Import ToolChoiceOption from sampling module (after other imports)
from fastmcp.server.sampling.run import ToolChoiceOption  # noqa: E402

# Warn-once guard for the sampling deprecation. Server-initiated createMessage
# was removed from MCP as of 2026-07-28 (SEP-2577); the warning fires a single
# time per process to flag that ctx.sample/ctx.sample_step are on their way out.
# A mutable set (mutated in place, never rebound) rather than a `global` boolean
# so the warn-once state is unambiguously read and written from the module.
_sample_deprecation_warned: set[bool] = set()

_SAMPLING_DEPRECATION_MESSAGE = (
    "ctx.sample() and ctx.sample_step() are deprecated and will be removed in a "
    "future FastMCP release. They rely on server-initiated createMessage "
    "requests, which were removed from MCP as of 2026-07-28 (SEP-2577), so they "
    "work only on session-based (handshake-era) connections. Call an LLM "
    "directly from your server instead."
)

_SAMPLING_MODERN_ERROR = (
    "server-initiated sampling is not available on MCP 2026-07-28 connections; "
    "SEP-2577 removed it — call an LLM from your server instead."
)

_ELICIT_MODERN_ERROR = (
    "elicitation via server-initiated requests is unavailable on 2026-07-28 "
    "connections."
)


def _warn_sampling_deprecated() -> None:
    """Emit the sampling deprecation warning once per process.

    Gated on ``settings.deprecation_warnings`` like every other FastMCP
    deprecation; fires a single time (module-level flag) rather than per call.
    """
    if _sample_deprecation_warned or not fastmcp.settings.deprecation_warnings:
        return
    _sample_deprecation_warned.add(True)
    warnings.warn(
        _SAMPLING_DEPRECATION_MESSAGE,
        FastMCPDeprecationWarning,
        stacklevel=3,
    )


_current_context: ContextVar[Context | None] = ContextVar("context", default=None)

TransportType = Literal["stdio", "sse", "streamable-http"]
_current_transport: ContextVar[TransportType | None] = ContextVar(
    "transport", default=None
)


def set_transport(
    transport: TransportType,
) -> Token[TransportType | None]:
    """Set the current transport type. Returns token for reset."""
    return _current_transport.set(transport)


def reset_transport(token: Token[TransportType | None]) -> None:
    """Reset transport to previous value."""
    _current_transport.reset(token)


@dataclass
class LogData:
    """Data object for passing log arguments to client-side handlers.

    This provides an interface to match the Python standard library logging,
    for compatibility with structured logging.
    """

    msg: str
    extra: Mapping[str, Any] | None = None


_mcp_level_to_python_level = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "alert": logging.CRITICAL,
    "emergency": logging.CRITICAL,
}


@contextmanager
def set_context(context: Context) -> Generator[Context, None, None]:
    token = _current_context.set(context)
    try:
        yield context
    finally:
        _current_context.reset(token)


@dataclass
class Context:
    """Context object providing access to MCP capabilities.

    This provides a cleaner interface to MCP's RequestContext functionality.
    It gets injected into tool and resource functions that request it via type hints.

    To use context in a tool function, add a parameter with the Context type annotation:

    ```python
    @server.tool
    async def my_tool(x: int, ctx: Context) -> str:
        # Log messages to the client
        await ctx.info(f"Processing {x}")
        await ctx.debug("Debug info")
        await ctx.warning("Warning message")
        await ctx.error("Error message")

        # Report progress
        await ctx.report_progress(50, 100, "Processing")

        # Access resources
        data = await ctx.read_resource("resource://data")

        # Get request info
        request_id = ctx.request_id
        client_id = ctx.client_id

        # Manage state across the session (persists across requests)
        await ctx.set_state("key", "value")
        value = await ctx.get_state("key")

        # Store non-serializable values for the current request only
        await ctx.set_state("client", http_client, serializable=False)

        return str(x)
    ```

    State Management:
    Context provides session-scoped state that persists across requests within
    the same MCP session. State is automatically keyed by session, ensuring
    isolation between different clients.

    State set during `on_initialize` middleware will persist to subsequent tool
    calls when using the same session object (STDIO, SSE, single-server HTTP).
    For distributed/serverless HTTP deployments where different machines handle
    the init and tool calls, state is isolated by the mcp-session-id header.

    The context parameter name can be anything as long as it's annotated with Context.
    The context is optional - tools that don't need it can omit the parameter.

    """

    # Default TTL for session state: 1 day in seconds
    _STATE_TTL_SECONDS: int = 86400

    def __init__(
        self,
        fastmcp: FastMCP,
        session: ServerSession | None = None,
        *,
        task_id: str | None = None,
        origin_request_id: str | None = None,
    ):
        self._fastmcp: weakref.ref[FastMCP] = weakref.ref(fastmcp)
        self._session: ServerSession | None = session  # For state ops during init
        self._tokens: list[Token] = []
        # Background task support (SEP-1686)
        self._task_id: str | None = task_id
        self._origin_request_id: str | None = origin_request_id
        # Request-scoped state for non-serializable values (serializable=False)
        self._request_state: dict[str, Any] = {}

    @property
    def is_background_task(self) -> bool:
        """True when this context is running in a background task (Docket worker).

        When True, certain operations like elicit() and sample() will use
        task-aware implementations that can pause the task and wait for
        client input.

        Example:
            ```python
            @server.tool(task=True)
            async def my_task(ctx: Context) -> str:
                # Works transparently in both foreground and background task modes
                result = await ctx.elicit("Need input", str)
                return str(result)
            ```
        """
        return self._task_id is not None

    @property
    def task_id(self) -> str | None:
        """Get the background task ID if running in a background task.

        Returns None if not running in a background task context.
        """
        return self._task_id

    @property
    def origin_request_id(self) -> str | None:
        """Get the request ID that originated this execution, if available.

        In foreground request mode, this is the current request_id.
        In background task mode, this is the request_id captured when the task
        was submitted, if one was available.
        """
        if self.request_context is not None:
            return str(self.request_context.request_id)
        return self._origin_request_id

    @property
    def fastmcp(self) -> FastMCP:
        """Get the FastMCP instance."""
        fastmcp = self._fastmcp()
        if fastmcp is None:
            raise RuntimeError("FastMCP instance is no longer available")
        return fastmcp

    async def __aenter__(self) -> Context:
        """Enter the context manager and set this context as the current context."""
        # Inherit request-scoped state from parent context so middleware
        # and tool contexts share the same in-memory state dict.
        parent = _current_context.get(None)
        if parent is not None:
            self._request_state = parent._request_state

        # Always set this context and save the token
        token = _current_context.set(self)
        self._tokens.append(token)

        # Set current server for dependency injection (use weakref to avoid reference cycles)
        from fastmcp.server.dependencies import (
            _current_docket,
            _current_server,
            _current_worker,
            is_docket_available,
        )

        self._server_token = _current_server.set(weakref.ref(self.fastmcp))

        # Re-set docket/worker from the server instance so mounted children
        # inherit the parent's Docket via the ContextVar. Only servers that
        # own the Docket (the parent) have _docket set; children skip this,
        # leaving the parent's value in place.
        if is_docket_available():
            server = self.fastmcp
            if server._docket is not None:
                self._docket_token = _current_docket.set(server._docket)
            if server._worker is not None:
                self._worker_token = _current_worker.set(server._worker)

        if not is_docket_available():
            # Without docket, the lifespan won't provide a SharedContext,
            # so create one scoped to this Context for Shared() dependencies.
            self._shared_context = SharedContext()
            await self._shared_context.__aenter__()

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager and reset the most recent token."""
        from fastmcp.server.dependencies import (
            _current_docket,
            _current_server,
            _current_worker,
        )

        if hasattr(self, "_worker_token"):
            _current_worker.reset(self._worker_token)
            del self._worker_token
        if hasattr(self, "_docket_token"):
            _current_docket.reset(self._docket_token)
            del self._docket_token
        if hasattr(self, "_shared_context"):
            await self._shared_context.__aexit__(exc_type, exc_val, exc_tb)
            del self._shared_context

        if hasattr(self, "_server_token"):
            _current_server.reset(self._server_token)
            del self._server_token

        # Reset context token
        if self._tokens:
            token = self._tokens.pop()
            _current_context.reset(token)

    @property
    def request_context(self) -> FastMCPRequestContext | None:
        """Access to the underlying request context.

        Returns None when the MCP session has not been established yet.
        Returns the FastMCPRequestContext wrapper once the MCP session is available.

        For HTTP request access in middleware, use `get_http_request()` from fastmcp.server.dependencies,
        which works whether or not the MCP session is available.

        Example in middleware:
        ```python
        async def on_request(self, context, call_next):
            ctx = context.fastmcp_context
            if ctx.request_context:
                # MCP session available - can access session_id, request_id, etc.
                session_id = ctx.session_id
            else:
                # MCP session not available yet - use HTTP helpers
                from fastmcp.server.dependencies import get_http_request
                request = get_http_request()
            return await call_next(context)
        ```
        """
        return fastmcp_request_ctx.get()

    def _input_response_params(
        self,
    ) -> mcp_types.InputResponseRequestParams | None:
        """The active request's multi-round-trip fields (SEP-2322), if any.

        Reads the raw params of the active wire request through the SDK's
        per-request context and reparses them as `InputResponseRequestParams`
        to recover the typed `input_responses` / `request_state`. The
        framework's request-state boundary has already unsealed `requestState`
        into plaintext by the time a handler observes it. Returns `None`
        outside a wire request (e.g. a background task) or when the params are
        not a mapping.
        """
        rc = self.request_context
        if rc is None:
            return None
        params = rc._srctx.params
        if not isinstance(params, Mapping):
            return None
        return mcp_types.InputResponseRequestParams.model_validate(dict(params))

    @property
    def input_responses(self) -> mcp_types.InputResponses | None:
        """Client responses to a prior `InputRequiredResult.input_requests`.

        The multi-round-trip guard channel (SEP-2322). A guard tool inspects
        this to decide what to do on each round: `None` on the initial round
        (nothing has been asked yet, or the client retried without responses),
        so the tool returns an `InputRequiredResult` to ask; present on a later
        round, so the tool reads the answers and proceeds. It is a mapping whose
        keys match the `input_requests` map the tool minted; each value is the
        client's result for that request (an `ElicitResult`, `CreateMessageResult`,
        or `ListRootsResult`).
        """
        params = self._input_response_params()
        return params.input_responses if params else None

    @property
    def request_state(self) -> str | None:
        """Opaque state echoed from a prior `InputRequiredResult.request_state`.

        The multi-round-trip guard channel (SEP-2322): whatever a tool put in
        `InputRequiredResult.request_state` on an earlier round is handed back
        here (as plaintext — the framework seals it on the wire and unseals it
        before the tool runs, so tampering is rejected before this is read).
        `None` on the initial round. Use it to carry a small amount of computed
        state across rounds without re-deriving it.
        """
        params = self._input_response_params()
        return params.request_state if params else None

    @property
    def lifespan_context(self) -> dict[str, Any]:
        """Access the server's lifespan context.

        Returns the context dict yielded by *this* server's lifespan function.
        For a mounted child this is the child's own lifespan, not the parent's
        — the MCP session always belongs to the parent, so reading from the
        request context would return the parent's. We read directly from the
        server's cached lifespan result instead, which is set by the
        per-server ``_lifespan_manager`` regardless of mount position.

        Returns an empty dict if no lifespan was configured.

        Example:
        ```python
        @server.tool
        def my_tool(ctx: Context) -> str:
            db = ctx.lifespan_context.get("db")
            if db:
                return db.query("SELECT 1")
            return "No database connection"
        ```
        """
        result = self.fastmcp._lifespan_result
        if result is not None:
            return result
        # Server's lifespan was never entered for this Context's server (or
        # yielded None). Fall back to the request context's lifespan, which
        # for a mounted child will be the parent's — preserved for parity
        # with prior behavior, but in normal operation a child's own
        # lifespan populates `_lifespan_result` and short-circuits above.
        rc = self.request_context
        if rc is None:
            return {}
        return rc.lifespan_context

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        """Report progress for the current operation.

        Works in both foreground (MCP progress notifications) and background
        (Docket task execution) contexts.

        Args:
            progress: Current progress value e.g. 24
            total: Optional total value e.g. 100
            message: Optional status message describing current progress
        """

        rc = self.request_context
        progress_token = (
            rc._srctx.meta.get("progress_token")
            if rc is not None and rc._srctx.meta is not None
            else None
        )

        # Foreground: Send MCP progress notification if we have a token
        if progress_token is not None:
            await self.session.send_progress_notification(
                progress_token=progress_token,
                progress=progress,
                total=total,
                message=message,
                related_request_id=self.request_id,
            )
            return

        # Background: Update Docket execution progress (stored in Redis)
        # This makes progress visible via tasks/get and notifications/tasks/status
        from fastmcp.server.dependencies import is_docket_available

        if not is_docket_available():
            return

        try:
            from docket.dependencies import current_execution

            execution = current_execution.get()

            # Update progress in Redis using Docket's progress API.
            # Docket only exposes increment() (relative), so we compute
            # the delta from the last reported value stored on this execution.
            if total is not None:
                await execution.progress.set_total(int(total))

            current = int(progress)
            last: int = getattr(execution, "_fastmcp_last_progress", 0)
            delta = current - last
            if delta > 0:
                await execution.progress.increment(delta)
            execution._fastmcp_last_progress = current  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]

            if message is not None:
                await execution.progress.set_message(message)
        except LookupError:
            # Not running in Docket worker context - no progress tracking available
            pass

    async def _paginate_list(
        self,
        call_handler: Callable[[Any, Any], Any],
        extract_items: Callable[[Any], list[Any]],
    ) -> list[Any]:
        """Generic pagination helper for list operations.

        Invokes a FastMCP ``_on_*`` list handler (``(ctx, params) -> result``)
        page by page. The SDK request context comes from the active request;
        outside a request context a fresh stand-in is used.

        Args:
            call_handler: FastMCP list handler taking ``(ctx, params)``.
            extract_items: Function to extract items from the result.

        Returns:
            List of all items across all pages
        """
        rc = self.request_context
        srctx = rc._srctx if rc is not None else _detached_request_context(self)

        all_items: list[Any] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params = mcp_types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await call_handler(srctx, params)
            all_items.extend(extract_items(result))
            if not result.next_cursor:
                break
            if result.next_cursor in seen_cursors:
                break
            seen_cursors.add(result.next_cursor)
            cursor = result.next_cursor
        return all_items

    async def list_resources(self) -> list[SDKResource]:
        """List all available resources from the server.

        Returns:
            List of Resource objects available on the server
        """
        return await self._paginate_list(
            call_handler=self.fastmcp._on_list_resources,
            extract_items=lambda result: result.resources,
        )

    async def list_prompts(self) -> list[SDKPrompt]:
        """List all available prompts from the server.

        Returns:
            List of Prompt objects available on the server
        """
        return await self._paginate_list(
            call_handler=self.fastmcp._on_list_prompts,
            extract_items=lambda result: result.prompts,
        )

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> GetPromptResult:
        """Get a prompt by name with optional arguments.

        Args:
            name: The name of the prompt to get
            arguments: Optional arguments to pass to the prompt

        Returns:
            The prompt result
        """
        result = await self.fastmcp.render_prompt(name, arguments)
        if isinstance(result, mcp_types.CreateTaskResult):
            raise RuntimeError(
                "Unexpected CreateTaskResult: Context calls should not have task metadata"
            )
        return result.to_mcp_prompt_result()

    async def read_resource(self, uri: str | AnyUrl) -> ResourceResult:
        """Read a resource by URI.

        Args:
            uri: Resource URI to read

        Returns:
            ResourceResult with contents
        """
        result = await self.fastmcp.read_resource(str(uri))
        if isinstance(result, mcp_types.CreateTaskResult):
            raise RuntimeError(
                "Unexpected CreateTaskResult: Context calls should not have task metadata"
            )
        return result

    async def log(
        self,
        message: str,
        level: LoggingLevel | None = None,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a log message to the client.

        Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`.

        Args:
            message: Log message
            level: Optional log level. One of "debug", "info", "notice", "warning", "error", "critical",
                "alert", or "emergency". Default is "info".
            logger_name: Optional logger name
            extra: Optional mapping for additional arguments
        """
        data = LogData(msg=message, extra=extra)
        related_request_id = self.origin_request_id

        # Resolve the client-requested minimum level (set via logging/setLevel),
        # keyed by session id, falling back to the server's configured default.
        min_level = self.fastmcp.client_log_level
        session = self.session
        session_min = self.fastmcp._client_log_levels.get(
            _log_level_session_key(session)
        )
        if session_min is not None:
            min_level = session_min

        await _log_to_server_and_client(
            data=data,
            session=session,
            level=level or "info",
            logger_name=logger_name,
            related_request_id=related_request_id,
            min_level=min_level,
        )

    @property
    def transport(self) -> TransportType | None:
        """Get the current transport type.

        Returns the transport type used to run this server: "stdio", "sse",
        or "streamable-http". Returns None if called outside of a server context.
        """
        return _current_transport.get()

    def client_supports_extension(self, extension_id: str) -> bool:
        """Check whether the connected client supports a given MCP extension.

        Inspects the ``extensions`` extra field on ``ClientCapabilities``
        sent by the client during initialization.

        Reads the client's advertised capabilities from the session, which is
        available in request mode and in background-task mode (where the
        snapshot session preserves the client's initialize params). Returns
        ``False`` when no session is available (e.g., a distributed worker with
        no live session, or outside any context) or when the client did not
        advertise the extension.

        Example::

            from fastmcp.apps.config import UI_EXTENSION_ID

            @mcp.tool
            async def my_tool(ctx: Context) -> str:
                if ctx.client_supports_extension(UI_EXTENSION_ID):
                    return "UI-capable client"
                return "text-only client"
        """
        try:
            session = self.session
        except RuntimeError:
            return False
        return client_supports_extension(session, extension_id)

    @property
    def client_id(self) -> str | None:
        """Get the client ID if available."""
        rc = self.request_context
        return (
            rc.meta.get("client_id") if rc is not None and rc.meta is not None else None
        )

    @property
    def request_id(self) -> str:
        """Get the unique ID for this request.

        Raises RuntimeError if MCP request context is not available.
        """
        if self.request_context is None:
            raise RuntimeError(
                "request_id is not available because the MCP session has not been established yet. "
                "Check `context.request_context` for None before accessing this attribute."
            )
        return str(self.request_context.request_id)

    @property
    def session_id(self) -> str:
        """Get the MCP session ID for ALL transports.

        Returns the session ID that can be used as a key for session-based
        data storage (e.g., Redis) to share data between tool calls within
        the same client session.

        Returns:
            The session ID for StreamableHTTP transports, or a generated ID
            for other transports.

        Raises:
            RuntimeError if no session is available.

        Example:
            ```python
            @server.tool
            def store_data(data: dict, ctx: Context) -> str:
                session_id = ctx.session_id
                redis_client.set(f"session:{session_id}:data", json.dumps(data))
                return f"Data stored for session {session_id}"
            ```
        """
        from uuid import uuid4

        # Get session from request context or _session (for on_initialize)
        request_ctx = self.request_context
        if request_ctx is not None:
            session = request_ctx.session
        elif self._session is not None:
            session = self._session
        else:
            raise RuntimeError(
                "session_id is not available because no session exists. "
                "This typically means you're outside a request context."
            )

        # In SDK v2 the ServerSession is constructed fresh per request, so the
        # stable per-client identity lives on the underlying Connection, which
        # persists for the whole client session. Cache the state prefix on the
        # connection (its `session_id` for HTTP, its `state` dict otherwise) so
        # session-scoped state survives across tool calls.
        connection = getattr(session, "_connection", None)

        # Check for a cached prefix on the stable connection (or the session, as
        # a fallback for on_initialize where only a raw session is available).
        if connection is not None:
            cached = connection.state.get("_fastmcp_state_prefix")
            if cached is not None:
                return cached
        session_cached = getattr(session, "_fastmcp_state_prefix", None)
        if session_cached is not None:
            return session_cached

        # For HTTP, prefer the connection's negotiated session id, then the
        # incoming request header.
        session_id: str | None = None
        if connection is not None:
            session_id = connection.session_id
        if session_id is None and request_ctx is not None:
            request = request_ctx.request
            if request:
                session_id = request.headers.get("mcp-session-id")

        # For STDIO/SSE/in-memory, generate a UUID.
        if session_id is None:
            session_id = str(uuid4())

        # Cache on the stable connection (falling back to the session).
        if connection is not None:
            connection.state["_fastmcp_state_prefix"] = session_id
        else:
            session._fastmcp_state_prefix = session_id  # type: ignore[attr-defined]  # ty:ignore[unresolved-attribute]
        return session_id

    @property
    def session(self) -> ServerSession:
        """Access to the underlying session for advanced usage.

        In request mode: Returns the session from the active request context.
        In background task mode: Returns the session stored at Context creation.

        Raises RuntimeError if no session is available.
        """
        # Background task mode: use the stored session
        if self.is_background_task and self._session is not None:
            return self._session

        # Request mode: use request context
        if self.request_context is not None:
            return self.request_context.session

        # Fallback to stored session (e.g., during on_initialize)
        if self._session is not None:
            return self._session

        raise RuntimeError(
            "session is not available because the MCP session has not been established yet. "
            "Check `context.request_context` for None before accessing this attribute."
        )

    # Convenience methods for common log levels
    async def debug(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a `DEBUG`-level message to the connected MCP Client.

        Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`."""
        await self.log(
            level="debug",
            message=message,
            logger_name=logger_name,
            extra=extra,
        )

    async def info(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a `INFO`-level message to the connected MCP Client.

        Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`."""
        await self.log(
            level="info",
            message=message,
            logger_name=logger_name,
            extra=extra,
        )

    async def warning(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a `WARNING`-level message to the connected MCP Client.

        Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`."""
        await self.log(
            level="warning",
            message=message,
            logger_name=logger_name,
            extra=extra,
        )

    async def error(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a `ERROR`-level message to the connected MCP Client.

        Messages sent to Clients are also logged to the `fastmcp.server.context.to_client` logger with a level of `DEBUG`."""
        await self.log(
            level="error",
            message=message,
            logger_name=logger_name,
            extra=extra,
        )

    async def list_roots(self) -> list[Root]:
        """List the roots available to the server, as indicated by the client."""
        # Deprecated upstream in SDK v2 but deliberately kept per compat directive;
        # removed with the multi-round-trip follow-up.
        result = await self.session.list_roots()  # ty: ignore[deprecated]
        return result.roots

    async def send_notification(
        self, notification: mcp_types.ServerNotification
    ) -> None:
        """Send a notification to the client immediately.

        Args:
            notification: An MCP notification instance (e.g., ToolListChangedNotification())
        """
        # v2: ServerNotification is a union of concrete notification models;
        # ServerSession.send_notification takes an instance directly (no wrapper).
        await self.session.send_notification(notification)

    async def close_sse_stream(self) -> None:
        """Close the current response stream to trigger client reconnection.

        When using StreamableHTTP transport with an EventStore configured, this
        method gracefully closes the HTTP connection for the current request.
        The client will automatically reconnect (after `retry_interval` milliseconds)
        and resume receiving events from where it left off via the EventStore.

        This is useful for long-running operations to avoid load balancer timeouts.
        Instead of holding a connection open for minutes, you can periodically close
        and let the client reconnect.

        Example:
            ```python
            @mcp.tool
            async def long_running_task(ctx: Context) -> str:
                for i in range(100):
                    await ctx.report_progress(i, 100)

                    # Close connection every 30 iterations to avoid LB timeouts
                    if i % 30 == 0 and i > 0:
                        await ctx.close_sse_stream()

                    await do_work()
                return "Done"
            ```

        Note:
            This is a no-op (with a debug log) if not using StreamableHTTP
            transport with an EventStore configured.
        """
        if not self.request_context or not self.request_context.close_sse_stream:
            logger.debug(
                "close_sse_stream() called but not applicable "
                "(requires StreamableHTTP transport with event_store)"
            )
            return
        await self.request_context.close_sse_stream()

    def _is_modern_protocol(self) -> bool:
        """True when the negotiated MCP protocol era removed the back-channel.

        Reads the negotiated protocol version from the active request context.
        The 2026-07-28 era (SEP-2577) has no back-channel for server-initiated
        requests such as sampling/elicitation. Returns False when no request
        context is available (e.g. background task or pre-session), leaving the
        existing wire path to surface its own error.
        """
        rc = self.request_context
        if rc is None:
            return False
        return rc.protocol_version in MODERN_PROTOCOL_VERSIONS

    def _server_can_sample(self) -> bool:
        """True when a server-configured sampling handler can serve the request
        without the client back-channel.

        FastMCP supports a server-side sampling handler (``FastMCP(sampling_handler=...)``).
        With ``sampling_handler_behavior="always"`` the handler always answers;
        with ``"fallback"`` it answers whenever the client cannot. On modern
        connections the client back-channel is gone, so either configuration lets
        the server answer entirely server-side as long as a handler is set. (For
        ``"always"`` without a handler the sampling implementation raises its own
        clear "no handler configured" error, which is not an era concern.)
        """
        fastmcp = self.fastmcp
        if fastmcp.sampling_handler_behavior == "always":
            return True
        return fastmcp.sampling_handler is not None

    async def sample_step(
        self,
        messages: str | Sequence[str | SamplingMessage],
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_preferences: ModelPreferences | str | list[str] | None = None,
        tools: Sequence[SamplingTool | Callable[..., Any]] | None = None,
        tool_choice: ToolChoiceOption | str | None = None,
        execute_tools: bool = True,
        mask_error_details: bool | None = None,
        tool_concurrency: int | None = None,
    ) -> SampleStep:
        """
        Make a single LLM sampling call.

        This is a stateless function that makes exactly one LLM call and optionally
        executes any requested tools. Use this for fine-grained control over the
        sampling loop.

        Args:
            messages: The message(s) to send. Can be a string, list of strings,
                or list of SamplingMessage objects.
            system_prompt: Optional system prompt for the LLM.
            temperature: Optional sampling temperature.
            max_tokens: Maximum tokens to generate. Defaults to 512.
            model_preferences: Optional model preferences.
            tools: Optional list of tools the LLM can use.
            tool_choice: Tool choice mode ("auto", "required", or "none").
            execute_tools: If True (default), execute tool calls and append results
                to history. If False, return immediately with tool_calls available
                in the step for manual execution.
            mask_error_details: If True, mask detailed error messages from tool
                execution. When None (default), uses the global settings value.
                Tools can raise ToolError to bypass masking.
            tool_concurrency: Controls parallel execution of tools:
                - None (default): Sequential execution (one at a time)
                - 0: Unlimited parallel execution
                - N > 0: Execute at most N tools concurrently
                If any tool has sequential=True, all tools execute sequentially
                regardless of this setting.

        Returns:
            SampleStep containing:
            - .response: The raw LLM response
            - .history: Messages including input, assistant response, and tool results
            - .is_tool_use: True if the LLM requested tool execution
            - .tool_calls: List of tool calls (if any)
            - .text: The text content (if any)

        Example:
            messages = "Research X"

            while True:
                step = await ctx.sample_step(messages, tools=[search])

                if not step.is_tool_use:
                    print(step.text)
                    break

                # Continue with tool results
                messages = step.history
        """
        _warn_sampling_deprecated()
        # On modern (2026-07-28) connections the client back-channel is gone
        # (SEP-2577). A server-configured sampling handler can still answer
        # entirely server-side; only raise the era error when nothing can serve
        # the request. When modern, force the handler path (never attempt the
        # dead client) by passing client_available=False.
        client_available = not self._is_modern_protocol()
        if not client_available and not self._server_can_sample():
            raise ToolError(_SAMPLING_MODERN_ERROR)
        return await sample_step_impl(
            self,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model_preferences=model_preferences,
            tools=tools,
            tool_choice=tool_choice,
            auto_execute_tools=execute_tools,
            mask_error_details=mask_error_details,
            tool_concurrency=tool_concurrency,
            client_available=client_available,
        )

    @overload
    async def sample(
        self,
        messages: str | Sequence[str | SamplingMessage],
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_preferences: ModelPreferences | str | list[str] | None = None,
        tools: Sequence[SamplingTool | Callable[..., Any]] | None = None,
        result_type: type[ResultT],
        mask_error_details: bool | None = None,
        tool_concurrency: int | None = None,
    ) -> SamplingResult[ResultT]:
        """Overload: With result_type, returns SamplingResult[ResultT]."""

    @overload
    async def sample(
        self,
        messages: str | Sequence[str | SamplingMessage],
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_preferences: ModelPreferences | str | list[str] | None = None,
        tools: Sequence[SamplingTool | Callable[..., Any]] | None = None,
        result_type: None = None,
        mask_error_details: bool | None = None,
        tool_concurrency: int | None = None,
    ) -> SamplingResult[str]:
        """Overload: Without result_type, returns SamplingResult[str]."""

    async def sample(
        self,
        messages: str | Sequence[str | SamplingMessage],
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_preferences: ModelPreferences | str | list[str] | None = None,
        tools: Sequence[SamplingTool | Callable[..., Any]] | None = None,
        result_type: type[ResultT] | None = None,
        mask_error_details: bool | None = None,
        tool_concurrency: int | None = None,
    ) -> SamplingResult[ResultT] | SamplingResult[str]:
        """
        Send a sampling request to the client and await the response.

        This method runs to completion automatically. When tools are provided,
        it executes a tool loop: if the LLM returns a tool use request, the tools
        are executed and the results are sent back to the LLM. This continues
        until the LLM provides a final text response.

        When result_type is specified, a synthetic `final_response` tool is
        created. The LLM calls this tool to provide the structured response,
        which is validated against the result_type and returned as `.result`.

        For fine-grained control over the sampling loop, use sample_step() instead.

        Args:
            messages: The message(s) to send. Can be a string, list of strings,
                or list of SamplingMessage objects.
            system_prompt: Optional system prompt for the LLM.
            temperature: Optional sampling temperature.
            max_tokens: Maximum tokens to generate. Defaults to 512.
            model_preferences: Optional model preferences.
            tools: Optional list of tools the LLM can use. Accepts plain
                functions or SamplingTools.
            result_type: Optional type for structured output. When specified,
                a synthetic `final_response` tool is created and the LLM's
                response is validated against this type.
            mask_error_details: If True, mask detailed error messages from tool
                execution. When None (default), uses the global settings value.
                Tools can raise ToolError to bypass masking.
            tool_concurrency: Controls parallel execution of tools:
                - None (default): Sequential execution (one at a time)
                - 0: Unlimited parallel execution
                - N > 0: Execute at most N tools concurrently
                If any tool has sequential=True, all tools execute sequentially
                regardless of this setting.

        Returns:
            SamplingResult[T] containing:
            - .text: The text representation (raw text or JSON for structured)
            - .result: The typed result (str for text, parsed object for structured)
            - .history: All messages exchanged during sampling

        Deprecated:
            Server-initiated sampling relies on the createMessage back-channel,
            which MCP removed as of 2026-07-28 (SEP-2577). This method works only
            on session-based (handshake-era) connections and will be removed in a
            future FastMCP release. Call an LLM directly from your server instead.
        """
        _warn_sampling_deprecated()
        # On modern (2026-07-28) connections the client back-channel is gone
        # (SEP-2577). A server-configured sampling handler can still answer
        # entirely server-side; only raise the era error when nothing can serve
        # the request. When modern, force the handler path (never attempt the
        # dead client) by passing client_available=False.
        client_available = not self._is_modern_protocol()
        if not client_available and not self._server_can_sample():
            raise ToolError(_SAMPLING_MODERN_ERROR)
        return await sample_impl(  # ty: ignore[invalid-return-type]
            self,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model_preferences=model_preferences,
            tools=tools,
            result_type=result_type,
            mask_error_details=mask_error_details,
            tool_concurrency=tool_concurrency,
            client_available=client_available,
        )

    @overload
    async def elicit(
        self,
        message: str,
        response_type: None,
        *,
        response_title: str | None = None,
        response_description: str | None = None,
    ) -> (
        AcceptedElicitation[dict[str, Any]] | DeclinedElicitation | CancelledElicitation
    ): ...

    """When response_type is None, the accepted elicitation will contain an
    empty dict"""

    @overload
    async def elicit(
        self,
        message: str,
        response_type: type[T],
        *,
        response_title: str | None = None,
        response_description: str | None = None,
    ) -> AcceptedElicitation[T] | DeclinedElicitation | CancelledElicitation: ...

    """When response_type is not None, the accepted elicitation will contain the
    response data"""

    @overload
    async def elicit(
        self,
        message: str,
        response_type: list[str],
        *,
        response_title: str | None = None,
        response_description: str | None = None,
    ) -> AcceptedElicitation[str] | DeclinedElicitation | CancelledElicitation: ...

    """When response_type is a list of strings, the accepted elicitation will
    contain the selected string response"""

    @overload
    async def elicit(
        self,
        message: str,
        response_type: dict[str, dict[str, str]],
        *,
        response_title: str | None = None,
        response_description: str | None = None,
    ) -> AcceptedElicitation[str] | DeclinedElicitation | CancelledElicitation: ...

    """When response_type is a dict mapping keys to title dicts, the accepted
    elicitation will contain the selected key"""

    @overload
    async def elicit(
        self,
        message: str,
        response_type: list[list[str]],
        *,
        response_title: str | None = None,
        response_description: str | None = None,
    ) -> (
        AcceptedElicitation[list[str]] | DeclinedElicitation | CancelledElicitation
    ): ...

    """When response_type is a list containing a list of strings (multi-select),
    the accepted elicitation will contain a list of selected strings"""

    @overload
    async def elicit(
        self,
        message: str,
        response_type: list[dict[str, dict[str, str]]],
        *,
        response_title: str | None = None,
        response_description: str | None = None,
    ) -> (
        AcceptedElicitation[list[str]] | DeclinedElicitation | CancelledElicitation
    ): ...

    """When response_type is a list containing a dict mapping keys to title dicts
    (multi-select with titles), the accepted elicitation will contain a list of
    selected keys"""

    async def elicit(
        self,
        message: str,
        response_type: type[T]
        | list[str]
        | dict[str, dict[str, str]]
        | list[list[str]]
        | list[dict[str, dict[str, str]]]
        | None = None,
        *,
        response_title: str | None = None,
        response_description: str | None = None,
    ) -> (
        AcceptedElicitation[T]
        | AcceptedElicitation[dict[str, Any]]
        | AcceptedElicitation[str]
        | AcceptedElicitation[list[str]]
        | DeclinedElicitation
        | CancelledElicitation
    ):
        """
        Send an elicitation request to the client and await the response.

        Call this method at any time to request additional information from
        the user through the client. The client must support elicitation,
        or the request will error.

        Note that the MCP protocol only supports simple object schemas with
        primitive types. You can provide a dataclass, TypedDict, or BaseModel to
        comply. If you provide a primitive type, an object schema with a single
        "value" field will be generated for the MCP interaction and
        automatically deconstructed into the primitive type upon response.

        Passing ``response_type=None`` (or omitting it) is deprecated and will
        be removed in a future version. The resulting empty-schema form-mode
        request is ambiguous and causes some clients (e.g. VS Code) to hang on
        an empty form. Pass an explicit ``response_type`` describing the data
        you want back.

        Args:
            message: A human-readable message explaining what information is needed
            response_type: The type of the response, which should be a primitive
                type or dataclass or BaseModel. If it is a primitive type, an
                object schema with a single "value" field will be generated.
            response_title: Optional label to display for the wrapped ``value``
                field when ``response_type`` is a scalar, Literal, Enum, or one
                of the dict/list shorthand forms. Overrides the auto-generated
                "Value" label. Raises ``TypeError`` if passed with a BaseModel,
                dataclass, or ``None`` response type (use ``Field(title=...)``
                on the model instead).
            response_description: Optional description to attach to the wrapped
                ``value`` field. Same scope rules as ``response_title``.

        Note:
            This method works transparently in both request and background task
            contexts. In background task mode (SEP-1686), it will set the task
            status to "input_required" and wait for the client to provide input.
        """
        if response_type is None and fastmcp.settings.deprecation_warnings:
            warnings.warn(
                "Calling ctx.elicit() without a response_type is deprecated "
                "and will be removed in a future version. The empty-schema "
                "form-mode request is ambiguous under the current MCP spec "
                "and causes some clients (e.g. VS Code) to render an empty, "
                "non-functional form. Pass an explicit response_type "
                "describing the data you expect back.",
                FastMCPDeprecationWarning,
                stacklevel=2,
            )
        config = parse_elicit_response_type(
            response_type,
            response_title=response_title,
            response_description=response_description,
        )

        if self.is_background_task:
            # Background task mode: use task-aware elicitation
            result = await self._elicit_for_task(
                message=message,
                schema=config.schema,
            )
        else:
            # Foreground push path: server-initiated elicitation needs a
            # back-channel, which the 2026-07-28 era removed (SEP-2577). Raise a
            # clear era-aware error before hitting the wire instead of the SDK's
            # opaque "Method not found". Handshake-era behavior is unchanged.
            if self._is_modern_protocol():
                raise ToolError(_ELICIT_MODERN_ERROR)
            # Standard request mode: use session.elicit directly
            result = await self.session.elicit(
                message=message,
                requested_schema=config.schema,
                related_request_id=self.request_id,
            )

        if result.action == "accept":
            return handle_elicit_accept(config, result.content)
        elif result.action == "decline":
            return DeclinedElicitation()
        elif result.action == "cancel":
            return CancelledElicitation()
        else:
            raise ValueError(f"Unexpected elicitation action: {result.action}")

    async def _elicit_for_task(
        self,
        message: str,
        schema: dict[str, Any],
    ) -> mcp_types.ElicitResult:
        """Send an elicitation request from a background task (SEP-1686).

        This method handles elicitation when running in a Docket worker context,
        where there's no active MCP request. It:
        1. Sets the task status to "input_required"
        2. Sends the elicitation request with task metadata
        3. Waits for the client to provide input via tasks/sendInput
        4. Returns the result and resumes task execution

        Args:
            message: The message to display to the user
            schema: The JSON schema for the expected response

        Returns:
            ElicitResult with the user's response

        Raises:
            RuntimeError: If not running in a background task context
        """
        if not self.is_background_task:
            raise RuntimeError(
                "_elicit_for_task called but not in a background task context"
            )

        # Import here to avoid circular imports and optional dependency issues
        from fastmcp.server.tasks.elicitation import elicit_for_task

        return await elicit_for_task(
            task_id=self._task_id,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            session=self._session,
            message=message,
            schema=schema,
            fastmcp=self.fastmcp,
        )

    def _make_state_key(self, key: str) -> str:
        """Create session-prefixed key for state storage."""
        return f"{self.session_id}:{key}"

    async def set_state(
        self, key: str, value: Any, *, serializable: bool = True
    ) -> None:
        """Set a value in the state store.

        By default, values are stored in the session-scoped state store and
        persist across requests within the same MCP session. Values must be
        JSON-serializable (dicts, lists, strings, numbers, etc.).

        For non-serializable values (e.g., HTTP clients, database connections),
        pass ``serializable=False``. These values are stored in a request-scoped
        dict and only live for the current MCP request (tool call, resource
        read, or prompt render). They will not be available in subsequent
        requests.

        The key is automatically prefixed with the session identifier.
        """
        prefixed_key = self._make_state_key(key)
        if not serializable:
            self._request_state[prefixed_key] = value
            return
        # Clear any request-scoped shadow so the session value is visible
        self._request_state.pop(prefixed_key, None)
        try:
            await self.fastmcp._state_store.put(
                key=prefixed_key,
                value=StateValue(value=value),
                ttl=self._STATE_TTL_SECONDS,
            )
        except Exception as e:
            # Catch serialization errors from Pydantic (ValueError) or
            # the key_value library (SerializationError). Both contain
            # "serialize" in the message. Other exceptions propagate as-is.
            if "serialize" in str(e).lower():
                raise TypeError(
                    f"Value for state key {key!r} is not serializable. "
                    f"Use set_state({key!r}, value, serializable=False) to store "
                    f"non-serializable values. Note: non-serializable state is "
                    f"request-scoped and will not persist across requests."
                ) from e
            raise

    async def get_state(self, key: str) -> Any:
        """Get a value from the state store.

        Checks request-scoped state first (set with ``serializable=False``),
        then falls back to the session-scoped state store.

        Returns None if the key is not found.
        """
        prefixed_key = self._make_state_key(key)
        if prefixed_key in self._request_state:
            return self._request_state[prefixed_key]
        result = await self.fastmcp._state_store.get(key=prefixed_key)
        return result.value if result is not None else None

    async def delete_state(self, key: str) -> None:
        """Delete a value from the state store.

        Removes from both request-scoped and session-scoped stores.
        """
        prefixed_key = self._make_state_key(key)
        self._request_state.pop(prefixed_key, None)
        await self.fastmcp._state_store.delete(key=prefixed_key)

    # -------------------------------------------------------------------------
    # Session visibility control
    # -------------------------------------------------------------------------

    async def _get_visibility_rules(self) -> list[dict[str, Any]]:
        """Load visibility rule dicts from session state."""
        return await _get_visibility_rules(self)

    async def _get_session_transforms(self) -> list[Visibility]:
        """Get session-specific Visibility transforms from state store."""
        return await _get_session_transforms(self)

    async def enable_components(
        self,
        *,
        names: set[str] | None = None,
        keys: set[str] | None = None,
        version: VersionSpec | None = None,
        tags: set[str] | None = None,
        components: set[Literal["tool", "resource", "template", "prompt"]]
        | None = None,
        match_all: bool = False,
    ) -> None:
        """Enable components matching criteria for this session only.

        Session rules override global transforms. Rules accumulate - each call
        adds a new rule to the session. Later marks override earlier ones
        (Visibility transform semantics).

        Sends notifications to this session only: ToolListChangedNotification,
        ResourceListChangedNotification, and PromptListChangedNotification.

        Args:
            names: Component names or URIs to match.
            keys: Component keys to match (e.g., {"tool:my_tool@v1"}).
            version: Component version spec to match.
            tags: Tags to match (component must have at least one).
            components: Component types to match (e.g., {"tool", "prompt"}).
            match_all: If True, matches all components regardless of other criteria.
        """
        await _enable_components(
            self,
            names=names,
            keys=keys,
            version=version,
            tags=tags,
            components=components,
            match_all=match_all,
        )

    async def disable_components(
        self,
        *,
        names: set[str] | None = None,
        keys: set[str] | None = None,
        version: VersionSpec | None = None,
        tags: set[str] | None = None,
        components: set[Literal["tool", "resource", "template", "prompt"]]
        | None = None,
        match_all: bool = False,
    ) -> None:
        """Disable components matching criteria for this session only.

        Session rules override global transforms. Rules accumulate - each call
        adds a new rule to the session. Later marks override earlier ones
        (Visibility transform semantics).

        Sends notifications to this session only: ToolListChangedNotification,
        ResourceListChangedNotification, and PromptListChangedNotification.

        Args:
            names: Component names or URIs to match.
            keys: Component keys to match (e.g., {"tool:my_tool@v1"}).
            version: Component version spec to match.
            tags: Tags to match (component must have at least one).
            components: Component types to match (e.g., {"tool", "prompt"}).
            match_all: If True, matches all components regardless of other criteria.
        """
        await _disable_components(
            self,
            names=names,
            keys=keys,
            version=version,
            tags=tags,
            components=components,
            match_all=match_all,
        )

    async def reset_visibility(self) -> None:
        """Clear all session visibility rules.

        Use this to reset session visibility back to global defaults.

        Sends notifications to this session only: ToolListChangedNotification,
        ResourceListChangedNotification, and PromptListChangedNotification.
        """
        await _reset_visibility(self)


_MCP_LEVEL_SEVERITY: dict[LoggingLevel, int] = {
    "debug": 0,
    "info": 1,
    "notice": 2,
    "warning": 3,
    "error": 4,
    "critical": 5,
    "alert": 6,
    "emergency": 7,
}


def _detached_request_context(context: Context) -> ServerRequestContext:
    """Build a minimal SDK request context for internal handler invocation.

    Used by ``Context._paginate_list`` when no request context is active (e.g.
    introspection outside a live request), so the ``_on_*`` list handlers have a
    context to bind. The list handlers only read ``self`` (the FastMCP server)
    to enumerate components, so a session-less context is sufficient.
    """
    return ServerRequestContext(
        session=cast(ServerSession, context._session),
        lifespan_context={},
        protocol_version="2025-06-18",
        method="internal",
        params=None,
        request_id=None,
        meta=None,
        request=None,
    )


def _log_level_session_key(session: ServerSession) -> str:
    """Derive the per-session key used for logging/setLevel gating.

    v2 constructs sessions per-request, so the stable identity is the
    connection session id (stateful HTTP). stdio/in-memory has no session id,
    so a sentinel key is used — all such connections share one gate, matching
    the single-connection nature of those transports.
    """
    connection = getattr(session, "_connection", None)
    session_id = getattr(connection, "session_id", None) if connection else None
    return session_id if session_id is not None else "__no_session__"


async def _log_to_server_and_client(
    data: LogData,
    session: ServerSession,
    level: LoggingLevel,
    logger_name: str | None = None,
    related_request_id: str | None = None,
    min_level: LoggingLevel | None = None,
) -> None:
    """Log a message to the server and client."""
    if min_level is not None:
        if _MCP_LEVEL_SEVERITY[level] < _MCP_LEVEL_SEVERITY[min_level]:
            return

    msg_prefix = f"Sending {level.upper()} to client"

    if logger_name:
        msg_prefix += f" ({logger_name})"

    to_client_logger.log(
        level=_mcp_level_to_python_level[level],
        msg=f"{msg_prefix}: {data.msg}",
        extra=data.extra,
    )

    # Deprecated upstream in SDK v2 but deliberately kept per compat directive;
    # removed with the multi-round-trip follow-up.
    await session.send_log_message(  # ty: ignore[deprecated]
        level=level,
        data=data,
        logger=logger_name,
        related_request_id=related_request_id,
    )
