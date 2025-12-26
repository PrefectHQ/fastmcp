from __future__ import annotations

import copy
import json
import logging
import weakref
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from logging import Logger
from typing import Any, Literal, cast, overload

import anyio
import mcp.types
from mcp import LoggingLevel, ServerSession
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.types import (
    CreateMessageResult,
    CreateMessageResultWithTools,
    GetPromptResult,
    ModelPreferences,
    Root,
    SamplingMessage,
    SamplingMessageContentBlock,
    TextContent,
    ToolChoice,
    ToolResultContent,
    ToolUseContent,
)
from mcp.types import Prompt as SDKPrompt
from mcp.types import Resource as SDKResource
from mcp.types import Tool as SDKTool
from pydantic import ValidationError
from pydantic.networks import AnyUrl
from starlette.requests import Request
from typing_extensions import TypeVar

from fastmcp import settings
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
    _parse_model_preferences,
    call_sampling_handler,
    determine_handler_mode,
)
from fastmcp.server.sampling.run import (
    execute_tools as run_sampling_tools,
)
from fastmcp.server.server import FastMCP
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import _clamp_logger, get_logger
from fastmcp.utilities.types import get_cached_typeadapter

logger: Logger = get_logger(name=__name__)
to_client_logger: Logger = logger.getChild(suffix="to_client")

# Convert all levels of server -> client messages to debug level
# This clamp can be undone at runtime by calling `_unclamp_logger` or calling
# `_clamp_logger` with a different max level.
_clamp_logger(logger=to_client_logger, max_level="DEBUG")


T = TypeVar("T", default=Any)
ResultT = TypeVar("ResultT", default=str)

# Simplified tool choice type - just the mode string instead of the full MCP object
ToolChoiceOption = Literal["auto", "required", "none"]

_current_context: ContextVar[Context | None] = ContextVar("context", default=None)  # type: ignore[assignment]


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

        # Manage state across the request
        ctx.set_state("key", "value")
        value = ctx.get_state("key")

        return str(x)
    ```

    State Management:
    Context objects maintain a state dictionary that can be used to store and share
    data across middleware and tool calls within a request. When a new context
    is created (nested contexts), it inherits a copy of its parent's state, ensuring
    that modifications in child contexts don't affect parent contexts.

    The context parameter name can be anything as long as it's annotated with Context.
    The context is optional - tools that don't need it can omit the parameter.

    """

    def __init__(self, fastmcp: FastMCP):
        self._fastmcp: weakref.ref[FastMCP] = weakref.ref(fastmcp)
        self._tokens: list[Token] = []
        self._notification_queue: list[mcp.types.ServerNotificationType] = []
        self._state: dict[str, Any] = {}
        self._exit_stack: AsyncExitStack | None = None
        self._cancel_scope: anyio.CancelScope | None = None

    @property
    def fastmcp(self) -> FastMCP:
        """Get the FastMCP instance."""
        fastmcp = self._fastmcp()
        if fastmcp is None:
            raise RuntimeError("FastMCP instance is no longer available")
        return fastmcp

    async def __aenter__(self) -> Context:
        """Enter the context manager and set this context as the current context."""
        parent_context = _current_context.get(None)
        if parent_context is not None:
            # Inherit state from parent context
            self._state = copy.deepcopy(parent_context._state)

        # Always set this context and save the token
        token = _current_context.set(self)
        self._tokens.append(token)

        # Set current server for dependency injection (use weakref to avoid reference cycles)
        from fastmcp.server.dependencies import _current_server

        self._server_token = _current_server.set(weakref.ref(self.fastmcp))

        # Start background notification flusher
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()
        tg = await self._exit_stack.enter_async_context(anyio.create_task_group())
        self._cancel_scope = anyio.CancelScope()
        tg.start_soon(self._periodic_flush)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager and reset the most recent token."""
        # Stop background flusher and do final flush
        if self._cancel_scope is not None:
            self._cancel_scope.cancel()
        await self._flush_notifications()
        if self._exit_stack is not None:
            await self._exit_stack.aclose()

        # Reset server token
        if hasattr(self, "_server_token"):
            from fastmcp.server.dependencies import _current_server

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

    async def list_resources(self) -> list[SDKResource]:
        """List all available resources from the server.

        Returns:
            List of Resource objects available on the server
        """
        return await self.fastmcp._list_resources_mcp()

    async def list_prompts(self) -> list[SDKPrompt]:
        """List all available prompts from the server.

        Returns:
            List of Prompt objects available on the server
        """
        return await self.fastmcp._list_prompts_mcp()

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
            RuntimeError if MCP request context is not available.

        Example:
            ```python
            @server.tool
            def store_data(data: dict, ctx: Context) -> str:
                session_id = ctx.session_id
                redis_client.set(f"session:{session_id}:data", json.dumps(data))
                return f"Data stored for session {session_id}"
            ```
        """
        request_ctx = self.request_context
        if request_ctx is None:
            raise RuntimeError(
                "session_id is not available because the MCP session has not been established yet. "
                "Check `context.request_context` for None before accessing this attribute."
            )
        session = request_ctx.session

        # Try to get the session ID from the session attributes
        session_id = getattr(session, "_fastmcp_id", None)
        if session_id is not None:
            return session_id

        # Try to get the session ID from the http request headers
        request = request_ctx.request
        if request:
            session_id = request.headers.get("mcp-session-id")

        # Generate a session ID if it doesn't exist.
        if session_id is None:
            from uuid import uuid4

            session_id = str(uuid4())

        # Save the session id to the session attributes
        session._fastmcp_id = session_id  # type: ignore[attr-defined]
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

        Use this in async code when you want the notification sent right away.
        For sync code, use send_notification_sync() which queues the notification
        for the background flusher.

        Args:
            notification: An MCP notification instance (e.g., ToolListChangedNotification())
        """
        await self.session.send_notification(mcp.types.ServerNotification(notification))

    def send_notification_sync(
        self, notification: mcp.types.ServerNotificationType
    ) -> None:
        """Queue a notification to be sent by the background flusher.

        Use this in sync code when you can't await. The notification will be
        sent within ~1 second by the background flusher.

        Args:
            notification: An MCP notification instance (e.g., ToolListChangedNotification())
        """
        self._notification_queue.append(notification)

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
        # Convert messages to SamplingMessage objects
        current_messages = _prepare_messages(messages)

        # Convert tools to SamplingTools
        sampling_tools = _prepare_tools(tools)
        sdk_tools: list[SDKTool] | None = (
            [t._to_sdk_tool() for t in sampling_tools] if sampling_tools else None
        )
        tool_map: dict[str, SamplingTool] = (
            {t.name: t for t in sampling_tools} if sampling_tools else {}
        )

        # Determine whether to use fallback handler or client
        use_fallback = determine_handler_mode(self, bool(sampling_tools))

        # Build tool choice
        effective_tool_choice: ToolChoice | None = None
        if tool_choice is not None:
            if tool_choice not in ("auto", "required", "none"):
                raise ValueError(
                    f"Invalid tool_choice: {tool_choice!r}. "
                    "Must be 'auto', 'required', or 'none'."
                )
            effective_tool_choice = ToolChoice(
                mode=cast(Literal["auto", "required", "none"], tool_choice)
            )

        # Effective max_tokens
        effective_max_tokens = max_tokens if max_tokens is not None else 512

        # Make the LLM call
        if use_fallback:
            response = await call_sampling_handler(
                self,
                current_messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=effective_max_tokens,
                model_preferences=model_preferences,
                sdk_tools=sdk_tools,
                tool_choice=effective_tool_choice,
            )
        else:
            response = await self.session.create_message(
                messages=current_messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=effective_max_tokens,
                model_preferences=_parse_model_preferences(model_preferences),
                tools=sdk_tools,
                tool_choice=effective_tool_choice,
                related_request_id=self.request_id,
            )

        # Check if this is a tool use response
        is_tool_use_response = (
            isinstance(response, CreateMessageResultWithTools)
            and response.stopReason == "toolUse"
        )

        # Always include the assistant response in history
        current_messages.append(
            SamplingMessage(role="assistant", content=response.content)
        )

        # If not a tool use, return immediately
        if not is_tool_use_response:
            return SampleStep(response=response, history=current_messages)

        # If not executing tools, return with assistant message but no tool results
        if not execute_tools:
            return SampleStep(response=response, history=current_messages)

        # Execute tools and add results to history
        step_tool_calls = _extract_tool_calls(response)
        if step_tool_calls:
            effective_mask = (
                mask_error_details
                if mask_error_details is not None
                else settings.mask_error_details
            )
            tool_results: list[SamplingMessageContentBlock] = await run_sampling_tools(  # type: ignore[assignment]
                step_tool_calls, tool_map, mask_error_details=effective_mask
            )

            if tool_results:
                current_messages.append(
                    SamplingMessage(
                        role="user",
                        content=tool_results,
                    )
                )

        return SampleStep(response=response, history=current_messages)

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
        # Safety limit to prevent infinite loops
        max_iterations = 100

        # Convert tools to SamplingTools
        sampling_tools = _prepare_tools(tools)

        # Handle structured output with result_type
        tool_choice: str | None = None
        if result_type is not None and result_type is not str:
            final_response_tool = _create_final_response_tool(result_type)
            sampling_tools = list(sampling_tools) if sampling_tools else []
            sampling_tools.append(final_response_tool)

            # Always require tool calls when result_type is set - the LLM must
            # eventually call final_response (text responses are not accepted)
            tool_choice = "required"

        # Convert messages for the loop
        current_messages: str | Sequence[str | SamplingMessage] = messages

        for _iteration in range(max_iterations):
            step = await self.sample_step(
                messages=current_messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                model_preferences=model_preferences,
                tools=sampling_tools,
                tool_choice=tool_choice,
                mask_error_details=mask_error_details,
            )

            # Check for final_response tool call for structured output
            if result_type is not None and result_type is not str and step.is_tool_use:
                for tool_call in step.tool_calls:
                    if tool_call.name == "final_response":
                        # Validate and return the structured result
                        type_adapter = get_cached_typeadapter(result_type)

                        # Unwrap if we wrapped primitives (non-object schemas)
                        input_data = tool_call.input
                        original_schema = compress_schema(
                            type_adapter.json_schema(), prune_titles=True
                        )
                        if (
                            original_schema.get("type") != "object"
                            and isinstance(input_data, dict)
                            and "value" in input_data
                        ):
                            input_data = input_data["value"]

                        try:
                            validated_result = type_adapter.validate_python(input_data)
                            text = json.dumps(
                                type_adapter.dump_python(validated_result, mode="json")
                            )
                            return SamplingResult(
                                text=text,
                                result=validated_result,
                                history=step.history,
                            )
                        except ValidationError as e:
                            # Validation failed - add error as tool result
                            step.history.append(
                                SamplingMessage(
                                    role="user",
                                    content=[
                                        ToolResultContent(
                                            type="tool_result",
                                            toolUseId=tool_call.id,
                                            content=[
                                                TextContent(
                                                    type="text",
                                                    text=(
                                                        f"Validation error: {e}. "
                                                        "Please try again with valid data."
                                                    ),
                                                )
                                            ],
                                            isError=True,
                                        )
                                    ],  # type: ignore[arg-type]
                                )
                            )

            # If not a tool use response, we're done
            if not step.is_tool_use:
                # For structured output, the LLM must use the final_response tool
                if result_type is not None and result_type is not str:
                    raise RuntimeError(
                        f"Expected structured output of type {result_type.__name__}, "
                        "but the LLM returned a text response instead of calling "
                        "the final_response tool."
                    )
                return SamplingResult(
                    text=step.text,
                    result=cast(ResultT, step.text if step.text else ""),
                    history=step.history,
                )

            # Continue with the updated history
            current_messages = step.history

            # After first iteration, reset tool_choice to auto
            tool_choice = None

        raise RuntimeError(f"Sampling exceeded maximum iterations ({max_iterations})")

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

    def set_state(self, key: str, value: Any) -> None:
        """Set a value in the context state."""
        self._state[key] = value

    def get_state(self, key: str) -> Any:
        """Get a value from the context state. Returns None if the key is not found."""
        return self._state.get(key)

    async def _periodic_flush(self) -> None:
        """Background task that flushes the notification queue every second."""
        with self._cancel_scope:  # type: ignore[union-attr]
            while True:
                await anyio.sleep(1)
                await self._flush_notifications()

    async def _flush_notifications(self) -> None:
        """Send all queued notifications."""
        if not self._notification_queue:
            return

        for notification in self._notification_queue:
            try:
                await self.send_notification(notification)
            except Exception:
                # Don't let notification failures break the request
                logger.debug(
                    f"Failed to send notification: {notification}", exc_info=True
                )
        self._notification_queue.clear()


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


def _create_final_response_tool(result_type: type) -> SamplingTool:
    """Create a synthetic 'final_response' tool for structured output.

    This tool is used to capture structured responses from the LLM.
    The tool's schema is derived from the result_type.
    """
    type_adapter = get_cached_typeadapter(result_type)
    schema = type_adapter.json_schema()
    schema = compress_schema(schema, prune_titles=True)

    # Tool parameters must be object-shaped. Wrap primitives in {"value": <schema>}
    if schema.get("type") != "object":
        schema = {
            "type": "object",
            "properties": {"value": schema},
            "required": ["value"],
        }

    # The fn just returns the input as-is (validation happens in the loop)
    def final_response(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    return SamplingTool(
        name="final_response",
        description=(
            "Call this tool to provide your final response. "
            "Use this when you have completed the task and are ready to return the result."
        ),
        parameters=schema,
        fn=final_response,
    )


def _extract_text_from_content(
    content: SamplingMessageContentBlock | list[SamplingMessageContentBlock],
) -> str | None:
    """Extract text from content block(s).

    Returns the text if content is a TextContent or list containing TextContent,
    otherwise returns None.
    """
    if isinstance(content, list):
        for block in content:
            if isinstance(block, TextContent):
                return block.text
        return None
    elif isinstance(content, TextContent):
        return content.text
    return None


def _prepare_messages(
    messages: str | Sequence[str | SamplingMessage],
) -> list[SamplingMessage]:
    """Convert various message formats to a list of SamplingMessage objects."""
    if isinstance(messages, str):
        return [
            SamplingMessage(
                content=TextContent(text=messages, type="text"), role="user"
            )
        ]
    else:
        return [
            SamplingMessage(content=TextContent(text=m, type="text"), role="user")
            if isinstance(m, str)
            else m
            for m in messages
        ]


def _prepare_tools(
    tools: Sequence[SamplingTool | Callable[..., Any]] | None,
) -> list[SamplingTool] | None:
    """Convert tools to SamplingTool objects."""
    if tools is None:
        return None

    sampling_tools: list[SamplingTool] = []
    for t in tools:
        if isinstance(t, SamplingTool):
            sampling_tools.append(t)
        elif callable(t):
            sampling_tools.append(SamplingTool.from_function(t))
        else:
            raise TypeError(f"Expected SamplingTool or callable, got {type(t)}")

    return sampling_tools if sampling_tools else None


def _extract_tool_calls(
    response: CreateMessageResult | CreateMessageResultWithTools,
) -> list[ToolUseContent]:
    """Extract tool calls from a response."""
    content = response.content
    if isinstance(content, list):
        return [c for c in content if isinstance(c, ToolUseContent)]
    elif isinstance(content, ToolUseContent):
        return [content]
    return []
