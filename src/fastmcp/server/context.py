from __future__ import annotations

import logging
import weakref
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from logging import Logger
from typing import Any, Literal, overload

import mcp.types
from mcp import LoggingLevel, ServerSession
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.types import (
    GetPromptResult,
    ModelPreferences,
    Root,
    SamplingMessage,
)
from mcp.types import Prompt as SDKPrompt
from mcp.types import Resource as SDKResource
from pydantic.networks import AnyUrl
from starlette.requests import Request
from typing_extensions import TypeVar

from fastmcp.resources.resource import ResourceResult
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    handle_elicit_accept,
    parse_elicit_response_type,
)
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

    def __init__(self, fastmcp: FastMCP, session: ServerSession | None = None):
        self._fastmcp: weakref.ref[FastMCP] = weakref.ref(fastmcp)
        self._session: ServerSession | None = session  # For state ops during init
        self._tokens: list[Token] = []

    @property
    def fastmcp(self) -> FastMCP:
        """Get the FastMCP instance."""
        fastmcp = self._fastmcp()
        if fastmcp is None:
            raise RuntimeError("FastMCP instance is no longer available")
        return fastmcp

    async def __aenter__(self) -> Context:
        """Enter the context manager and set this context as the current context."""
        # Always set this context and save the token
        token = _current_context.set(self)
        self._tokens.append(token)

        # Set current server for dependency injection (use weakref to avoid reference cycles)
        from fastmcp.server.dependencies import (
            _current_docket,
            _current_server,
            _current_worker,
        )

        self._server_token = _current_server.set(weakref.ref(self.fastmcp))

        # Set docket/worker from server instance for this request's context.
        # This ensures ContextVars work even in ASGI environments (Lambda, FastAPI mount)
        # where lifespan ContextVars don't propagate to request handlers.
        server = self.fastmcp
        if server._docket is not None:
            self._docket_token = _current_docket.set(server._docket)

        if server._worker is not None:
            self._worker_token = _current_worker.set(server._worker)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager and reset the most recent token."""
        # Reset server/docket/worker tokens
        from fastmcp.server.dependencies import (
            _current_docket,
            _current_server,
            _current_worker,
        )

        if hasattr(self, "_worker_token"):
            _current_worker.reset(self._worker_token)
            delattr(self, "_worker_token")
        if hasattr(self, "_docket_token"):
            _current_docket.reset(self._docket_token)
            delattr(self, "_docket_token")
        if hasattr(self, "_server_token"):
            _current_server.reset(self._server_token)
            delattr(self, "_server_token")

        # Reset context token
        if self._tokens:
            token = self._tokens.pop()
            _current_context.reset(token)

    @property
    def request_context(self) -> RequestContext[ServerSession, Any, Request] | None:
        """Access to the underlying request context.

        Returns None when the MCP session has not been established yet.
        Returns the full RequestContext once the MCP session is available.

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
        try:
            return request_ctx.get()
        except LookupError:
            return None

    @property
    def lifespan_context(self) -> dict[str, Any]:
        """Access the server's lifespan context.

        Returns the context dict yielded by the server's lifespan function.
        Returns an empty dict if no lifespan was configured or if the MCP
        session is not yet established.

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
        rc = self.request_context
        if rc is None:
            return {}
        return rc.lifespan_context

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        """Report progress for the current operation.

        Args:
            progress: Current progress value e.g. 24
            total: Optional total value e.g. 100
        """

        progress_token = (
            self.request_context.meta.progressToken
            if self.request_context and self.request_context.meta
            else None
        )

        if progress_token is None:
            return

        await self.session.send_progress_notification(
            progress_token=progress_token,
            progress=progress,
            total=total,
            message=message,
            related_request_id=self.request_id,
        )

    async def _paginate_list(
        self,
        request_factory: Callable[[str | None], Any],
        call_method: Callable[[Any], Any],
        extract_items: Callable[[Any], list[Any]],
    ) -> list[Any]:
        """Generic pagination helper for list operations.

        Args:
            request_factory: Function that creates a request from a cursor
            call_method: Async method to call with the request
            extract_items: Function to extract items from the result

        Returns:
            List of all items across all pages
        """
        all_items: list[Any] = []
        cursor: str | None = None
        while True:
            request = request_factory(cursor)
            result = await call_method(request)
            all_items.extend(extract_items(result))
            if result.nextCursor is None:
                break
            cursor = result.nextCursor
        return all_items

    async def list_resources(self) -> list[SDKResource]:
        """List all available resources from the server.

        Returns:
            List of Resource objects available on the server
        """
        return await self._paginate_list(
            request_factory=lambda cursor: mcp.types.ListResourcesRequest(
                params=mcp.types.PaginatedRequestParams(cursor=cursor)
                if cursor
                else None
            ),
            call_method=self.fastmcp._list_resources_mcp,
            extract_items=lambda result: result.resources,
        )

    async def list_prompts(self) -> list[SDKPrompt]:
        """List all available prompts from the server.

        Returns:
            List of Prompt objects available on the server
        """
        return await self._paginate_list(
            request_factory=lambda cursor: mcp.types.ListPromptsRequest(
                params=mcp.types.PaginatedRequestParams(cursor=cursor)
                if cursor
                else None
            ),
            call_method=self.fastmcp._list_prompts_mcp,
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
        if isinstance(result, mcp.types.CreateTaskResult):
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
        if isinstance(result, mcp.types.CreateTaskResult):
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

        await _log_to_server_and_client(
            data=data,
            session=self.session,
            level=level or "info",
            logger_name=logger_name,
            related_request_id=self.request_id,
        )

    @property
    def transport(self) -> TransportType | None:
        """Get the current transport type.

        Returns the transport type used to run this server: "stdio", "sse",
        or "streamable-http". Returns None if called outside of a server context.
        """
        return _current_transport.get()

    @property
    def client_id(self) -> str | None:
        """Get the client ID if available."""
        return (
            getattr(self.request_context.meta, "client_id", None)
            if self.request_context and self.request_context.meta
            else None
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

        # Check for cached session ID
        session_id = getattr(session, "_fastmcp_state_prefix", None)
        if session_id is not None:
            return session_id

        # For HTTP, try to get from header
        if request_ctx is not None:
            request = request_ctx.request
            if request:
                session_id = request.headers.get("mcp-session-id")

        # For STDIO/SSE/in-memory, generate a UUID
        if session_id is None:
            session_id = str(uuid4())

        # Cache on session for consistency
        session._fastmcp_state_prefix = session_id  # type: ignore[attr-defined]
        return session_id

    @property
    def session(self) -> ServerSession:
        """Access to the underlying session for advanced usage.

        Raises RuntimeError if MCP request context is not available.
        """
        if self.request_context is None:
            raise RuntimeError(
                "session is not available because the MCP session has not been established yet. "
                "Check `context.request_context` for None before accessing this attribute."
            )
        return self.request_context.session

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
        result = await self.session.list_roots()
        return result.roots

    async def send_notification(
        self, notification: mcp.types.ServerNotificationType
    ) -> None:
        """Send a notification to the client immediately.

        Args:
            notification: An MCP notification instance (e.g., ToolListChangedNotification())
        """
        await self.session.send_notification(mcp.types.ServerNotification(notification))

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

        Returns:
            SamplingResult[T] containing:
            - .text: The text representation (raw text or JSON for structured)
            - .result: The typed result (str for text, parsed object for structured)
            - .history: All messages exchanged during sampling
        """
        return await sample_impl(
            self,
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model_preferences=model_preferences,
            tools=tools,
            result_type=result_type,
            mask_error_details=mask_error_details,
        )

    @overload
    async def elicit(
        self,
        message: str,
        response_type: None,
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
    ) -> AcceptedElicitation[T] | DeclinedElicitation | CancelledElicitation: ...

    """When response_type is not None, the accepted elicitation will contain the
    response data"""

    @overload
    async def elicit(
        self,
        message: str,
        response_type: list[str],
    ) -> AcceptedElicitation[str] | DeclinedElicitation | CancelledElicitation: ...

    """When response_type is a list of strings, the accepted elicitation will
    contain the selected string response"""

    @overload
    async def elicit(
        self,
        message: str,
        response_type: dict[str, dict[str, str]],
    ) -> AcceptedElicitation[str] | DeclinedElicitation | CancelledElicitation: ...

    """When response_type is a dict mapping keys to title dicts, the accepted
    elicitation will contain the selected key"""

    @overload
    async def elicit(
        self,
        message: str,
        response_type: list[list[str]],
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

        If the response_type is None, the generated schema will be that of an
        empty object in order to comply with the MCP protocol requirements.
        Clients must send an empty object ("{}")in response.

        Args:
            message: A human-readable message explaining what information is needed
            response_type: The type of the response, which should be a primitive
                type or dataclass or BaseModel. If it is a primitive type, an
                object schema with a single "value" field will be generated.
        """
        config = parse_elicit_response_type(response_type)

        result = await self.session.elicit(
            message=message,
            requestedSchema=config.schema,
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

    def _make_state_key(self, key: str) -> str:
        """Create session-prefixed key for state storage."""
        return f"{self.session_id}:{key}"

    async def set_state(self, key: str, value: Any) -> None:
        """Set a value in the session-scoped state store.

        Values persist across requests within the same MCP session.
        The key is automatically prefixed with the session identifier.
        State expires after 1 day to prevent unbounded memory growth.
        """
        prefixed_key = self._make_state_key(key)
        await self.fastmcp._state_store.put(
            key=prefixed_key,
            value=StateValue(value=value),
            ttl=self._STATE_TTL_SECONDS,
        )

    async def get_state(self, key: str) -> Any:
        """Get a value from the session-scoped state store.

        Returns None if the key is not found.
        """
        prefixed_key = self._make_state_key(key)
        result = await self.fastmcp._state_store.get(key=prefixed_key)
        return result.value if result is not None else None

    async def delete_state(self, key: str) -> None:
        """Delete a value from the session-scoped state store."""
        prefixed_key = self._make_state_key(key)
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


async def _log_to_server_and_client(
    data: LogData,
    session: ServerSession,
    level: LoggingLevel,
    logger_name: str | None = None,
    related_request_id: str | None = None,
) -> None:
    """Log a message to the server and client."""

    msg_prefix = f"Sending {level.upper()} to client"

    if logger_name:
        msg_prefix += f" ({logger_name})"

    to_client_logger.log(
        level=_mcp_level_to_python_level[level],
        msg=f"{msg_prefix}: {data.msg}",
        extra=data.extra,
    )

    await session.send_log_message(
        level=level,
        data=data,
        logger=logger_name,
        related_request_id=related_request_id,
    )
