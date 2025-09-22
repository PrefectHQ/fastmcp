from __future__ import annotations

import asyncio
import copy
import inspect
import warnings
import weakref
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, cast, get_origin, overload

from mcp import LoggingLevel, ServerSession
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.types import (
    AudioContent,
    ClientCapabilities,
    CreateMessageResult,
    ImageContent,
    IncludeContext,
    ModelHint,
    ModelPreferences,
    Root,
    SamplingCapability,
    SamplingMessage,
    TextContent,
)
from mcp.types import CreateMessageRequestParams as SamplingParams
from pydantic.networks import AnyUrl
from starlette.requests import Request
from typing_extensions import TypeVar

import fastmcp.server.dependencies
from fastmcp import settings
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    ScalarElicitationType,
    get_elicitation_schema,
)
from fastmcp.server.server import FastMCP
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import get_cached_typeadapter

logger = get_logger(__name__)

T = TypeVar("T", default=Any)
_current_context: ContextVar[Context | None] = ContextVar("context", default=None)  # type: ignore[assignment]
_flush_lock = asyncio.Lock()


@dataclass
class LogData:
    """Data object for passing log arguments to client-side handlers.

    This provides an interface to match the Python standard library logging,
    for compatibility with structured logging.
    """

    msg: str
    extra: Mapping[str, Any] | None = None


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
        self._notification_queue: set[str] = set()  # Dedupe notifications
        self._state: dict[str, Any] = {}

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
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager and reset the most recent token."""
        # Flush any remaining notifications before exiting
        await self._flush_notifications()

        if self._tokens:
            token = self._tokens.pop()
            _current_context.reset(token)

    @property
    def request_context(self) -> RequestContext[ServerSession, Any, Request]:
        """Access to the underlying request context.

        If called outside of a request context, this will raise a ValueError.
        """
        try:
            return request_ctx.get()
        except LookupError:
            raise ValueError("Context is not available outside of a request")

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
            if self.request_context.meta
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

    async def read_resource(self, uri: str | AnyUrl) -> list[ReadResourceContents]:
        """Read a resource by URI.

        Args:
            uri: Resource URI to read

        Returns:
            The resource content as either text or bytes
        """
        if self.fastmcp is None:
            raise ValueError("Context is not available outside of a request")
        return await self.fastmcp._mcp_read_resource(uri)

    async def log(
        self,
        message: str,
        level: LoggingLevel | None = None,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a log message to the client.

        Args:
            message: Log message
            level: Optional log level. One of "debug", "info", "notice", "warning", "error", "critical",
                "alert", or "emergency". Default is "info".
            logger_name: Optional logger name
            extra: Optional mapping for additional arguments
        """
        if level is None:
            level = "info"
        data = LogData(msg=message, extra=extra)
        await self.session.send_log_message(
            level=level,
            data=data,
            logger=logger_name,
            related_request_id=self.request_id,
        )

    @property
    def client_id(self) -> str | None:
        """Get the client ID if available."""
        return (
            getattr(self.request_context.meta, "client_id", None)
            if self.request_context.meta
            else None
        )

    @property
    def request_id(self) -> str:
        """Get the unique ID for this request."""
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
        setattr(session, "_fastmcp_id", session_id)
        return session_id

    @property
    def session(self) -> ServerSession:
        """Access to the underlying session for advanced usage."""
        return self.request_context.session

    # Convenience methods for common log levels
    async def debug(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a debug log message."""
        await self.log(
            level="debug", message=message, logger_name=logger_name, extra=extra
        )

    async def info(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send an info log message."""
        await self.log(
            level="info", message=message, logger_name=logger_name, extra=extra
        )

    async def warning(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a warning log message."""
        await self.log(
            level="warning", message=message, logger_name=logger_name, extra=extra
        )

    async def error(
        self,
        message: str,
        logger_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Send an error log message."""
        await self.log(
            level="error", message=message, logger_name=logger_name, extra=extra
        )

    async def list_roots(self) -> list[Root]:
        """List the roots available to the server, as indicated by the client."""
        result = await self.session.list_roots()
        return result.roots

    async def send_tool_list_changed(self) -> None:
        """Send a tool list changed notification to the client."""
        await self.session.send_tool_list_changed()

    async def send_resource_list_changed(self) -> None:
        """Send a resource list changed notification to the client."""
        await self.session.send_resource_list_changed()

    async def send_prompt_list_changed(self) -> None:
        """Send a prompt list changed notification to the client."""
        await self.session.send_prompt_list_changed()

    async def sample(
        self,
        messages: str | Sequence[str | SamplingMessage],
        system_prompt: str | None = None,
        include_context: IncludeContext | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_preferences: ModelPreferences | str | list[str] | None = None,
    ) -> TextContent | ImageContent | AudioContent:
        """
        Send a sampling request to the client and await the response.

        Call this method at any time to have the server request an LLM
        completion from the client. The client must be appropriately configured,
        or the request will error.
        """

        if max_tokens is None:
            max_tokens = 512

        if isinstance(messages, str):
            sampling_messages = [
                SamplingMessage(
                    content=TextContent(text=messages, type="text"), role="user"
                )
            ]
        elif isinstance(messages, Sequence):
            sampling_messages = [
                SamplingMessage(content=TextContent(text=m, type="text"), role="user")
                if isinstance(m, str)
                else m
                for m in messages
            ]

        should_fallback = (
            self.fastmcp.sampling_handler_behavior == "fallback"
            and not self.session.check_client_capability(
                capability=ClientCapabilities(sampling=SamplingCapability())
            )
        )

        if self.fastmcp.sampling_handler_behavior == "always" or should_fallback:
            if self.fastmcp.sampling_handler is None:
                raise ValueError("Client does not support sampling")

            create_message_result = self.fastmcp.sampling_handler(
                sampling_messages,
                SamplingParams(
                    systemPrompt=system_prompt,
                    messages=sampling_messages,
                    temperature=temperature,
                    maxTokens=max_tokens,
                    modelPreferences=_parse_model_preferences(model_preferences),
                ),
                self.request_context,
            )

            if inspect.isawaitable(create_message_result):
                create_message_result = await create_message_result

            if isinstance(create_message_result, str):
                return TextContent(text=create_message_result, type="text")

            if isinstance(create_message_result, CreateMessageResult):
                return create_message_result.content

            else:
                raise ValueError(
                    f"Unexpected sampling handler result: {create_message_result}"
                )

        result: CreateMessageResult = await self.session.create_message(
            messages=sampling_messages,
            system_prompt=system_prompt,
            include_context=include_context,
            temperature=temperature,
            max_tokens=max_tokens,
            model_preferences=_parse_model_preferences(model_preferences),
            related_request_id=self.request_id,
        )

        return result.content

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

    async def elicit(
        self,
        message: str,
        response_type: type[T] | list[str] | None = None,
    ) -> (
        AcceptedElicitation[T]
        | AcceptedElicitation[dict[str, Any]]
        | AcceptedElicitation[str]
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
        if response_type is None:
            schema = {"type": "object", "properties": {}}
        else:
            # if the user provided a list of strings, treat it as a Literal
            if isinstance(response_type, list):
                if not all(isinstance(item, str) for item in response_type):
                    raise ValueError(
                        "List of options must be a list of strings. Received: "
                        f"{response_type}"
                    )
                # Convert list of options to Literal type and wrap
                choice_literal = Literal[tuple(response_type)]  # type: ignore
                response_type = ScalarElicitationType[choice_literal]  # type: ignore
            # if the user provided a primitive scalar, wrap it in an object schema
            elif response_type in {bool, int, float, str}:
                response_type = ScalarElicitationType[response_type]  # type: ignore
            # if the user provided a Literal type, wrap it in an object schema
            elif get_origin(response_type) is Literal:
                response_type = ScalarElicitationType[response_type]  # type: ignore
            # if the user provided an Enum type, wrap it in an object schema
            elif isinstance(response_type, type) and issubclass(response_type, Enum):
                response_type = ScalarElicitationType[response_type]  # type: ignore

            response_type = cast(type[T], response_type)

            schema = get_elicitation_schema(response_type)

        result = await self.session.elicit(
            message=message,
            requestedSchema=schema,
            related_request_id=self.request_id,
        )

        if result.action == "accept":
            if response_type is not None:
                type_adapter = get_cached_typeadapter(response_type)
                validated_data = cast(
                    T | ScalarElicitationType[T],
                    type_adapter.validate_python(result.content),
                )
                if isinstance(validated_data, ScalarElicitationType):
                    return AcceptedElicitation[T](data=validated_data.value)
                else:
                    return AcceptedElicitation[T](data=cast(T, validated_data))
            elif result.content:
                raise ValueError(
                    "Elicitation expected an empty response, but received: "
                    f"{result.content}"
                )
            else:
                return AcceptedElicitation[dict[str, Any]](data={})
        elif result.action == "decline":
            return DeclinedElicitation()
        elif result.action == "cancel":
            return CancelledElicitation()
        else:
            # This should never happen, but handle it just in case
            raise ValueError(f"Unexpected elicitation action: {result.action}")

    def get_http_request(self) -> Request:
        """Get the active starlette request."""

        # Deprecated in 2.2.11
        if settings.deprecation_warnings:
            warnings.warn(
                "Context.get_http_request() is deprecated and will be removed in a future version. "
                "Use get_http_request() from fastmcp.server.dependencies instead. "
                "See https://gofastmcp.com/servers/context#http-requests for more details.",
                DeprecationWarning,
                stacklevel=2,
            )

        return fastmcp.server.dependencies.get_http_request()

    def set_state(self, key: str, value: Any) -> None:
        """Set a value in the context state."""
        self._state[key] = value

    def get_state(self, key: str) -> Any:
        """Get a value from the context state. Returns None if the key is not found."""
        return self._state.get(key)

    def _queue_tool_list_changed(self) -> None:
        """Queue a tool list changed notification."""
        self._notification_queue.add("notifications/tools/list_changed")
        self._try_flush_notifications()

    def _queue_resource_list_changed(self) -> None:
        """Queue a resource list changed notification."""
        self._notification_queue.add("notifications/resources/list_changed")
        self._try_flush_notifications()

    def _queue_prompt_list_changed(self) -> None:
        """Queue a prompt list changed notification."""
        self._notification_queue.add("notifications/prompts/list_changed")
        self._try_flush_notifications()

    def _try_flush_notifications(self) -> None:
        """Synchronous method that attempts to flush notifications if we're in an async context."""
        try:
            # Check if we're in an async context
            loop = asyncio.get_running_loop()
            if loop and not loop.is_running():
                return
            # Schedule flush as a task (fire-and-forget)
            asyncio.create_task(self._flush_notifications())
        except RuntimeError:
            # No event loop - will flush later
            pass

    async def _flush_notifications(self) -> None:
        """Send all queued notifications."""
        async with _flush_lock:
            if not self._notification_queue:
                return

            try:
                if "notifications/tools/list_changed" in self._notification_queue:
                    await self.session.send_tool_list_changed()
                if "notifications/resources/list_changed" in self._notification_queue:
                    await self.session.send_resource_list_changed()
                if "notifications/prompts/list_changed" in self._notification_queue:
                    await self.session.send_prompt_list_changed()
                self._notification_queue.clear()
            except Exception:
                # Don't let notification failures break the request
                pass


def _parse_model_preferences(
    model_preferences: ModelPreferences | str | list[str] | None,
) -> ModelPreferences | None:
    """
    Validates and converts user input for model_preferences into a ModelPreferences object.

    Args:
        model_preferences (ModelPreferences | str | list[str] | None):
            The model preferences to use. Accepts:
            - ModelPreferences (returns as-is)
            - str (single model hint)
            - list[str] (multiple model hints)
            - None (no preferences)

    Returns:
        ModelPreferences | None: The parsed ModelPreferences object, or None if not provided.

    Raises:
        ValueError: If the input is not a supported type or contains invalid values.
    """
    if model_preferences is None:
        return None
    elif isinstance(model_preferences, ModelPreferences):
        return model_preferences
    elif isinstance(model_preferences, str):
        # Single model hint
        return ModelPreferences(hints=[ModelHint(name=model_preferences)])
    elif isinstance(model_preferences, list):
        # List of model hints (strings)
        if not all(isinstance(h, str) for h in model_preferences):
            raise ValueError(
                "All elements of model_preferences list must be"
                " strings (model name hints)."
            )
        return ModelPreferences(hints=[ModelHint(name=h) for h in model_preferences])
    else:
        raise ValueError(
            "model_preferences must be one of: ModelPreferences, str, list[str], or None."
        )
