from __future__ import annotations as _annotations

import warnings
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from mcp import LoggingLevel
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.context import RequestContext
from mcp.types import (
    CreateMessageResult,
    ImageContent,
    ModelHint,
    ModelPreferences,
    Root,
    SamplingMessage,
    TextContent,
)
from pydantic.networks import AnyUrl
from starlette.requests import Request

import fastmcp.server.dependencies
from fastmcp.server.server import FastMCP
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

_current_context: ContextVar[Context | None] = ContextVar("context", default=None)


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
    def my_tool(x: int, ctx: Context) -> str:
        # Log messages to the client
        ctx.info(f"Processing {x}")
        ctx.debug("Debug info")
        ctx.warning("Warning message")
        ctx.error("Error message")

        # Report progress
        ctx.report_progress(50, 100, "Processing")

        # Access resources
        data = ctx.read_resource("resource://data")

        # Get request info
        request_id = ctx.request_id
        client_id = ctx.client_id

        return str(x)
    ```

    The context parameter name can be anything as long as it's annotated with Context.
    The context is optional - tools that don't need it can omit the parameter.

    """

    def __init__(self, fastmcp: FastMCP):
        self.fastmcp = fastmcp
        self._tokens: list[Token] = []

    def __enter__(self) -> Context:
        """Enter the context manager and set this context as the current context."""
        # Always set this context and save the token
        token = _current_context.set(self)
        self._tokens.append(token)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager and reset the most recent token."""
        if self._tokens:
            token = self._tokens.pop()
            _current_context.reset(token)

    @property
    def request_context(self) -> RequestContext:
        """Access to the underlying request context."""
        return self.fastmcp._mcp_server.request_context

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

        await self.request_context.session.send_progress_notification(
            progress_token=progress_token,
            progress=progress,
            total=total,
            message=message,
        )

    async def read_resource(self, uri: str | AnyUrl) -> list[ReadResourceContents]:
        """Read a resource by URI.

        Args:
            uri: Resource URI to read

        Returns:
            The resource content as either text or bytes
        """
        assert self.fastmcp is not None, "Context is not available outside of a request"
        return await self.fastmcp._mcp_read_resource(uri)

    async def log(
        self,
        message: str,
        level: LoggingLevel | None = None,
        logger_name: str | None = None,
    ) -> None:
        """Send a log message to the client.

        Args:
            message: Log message
            level: Optional log level. One of "debug", "info", "notice", "warning", "error", "critical",
                "alert", or "emergency". Default is "info".
            logger_name: Optional logger name
        """
        if level is None:
            level = "info"
        await self.request_context.session.send_log_message(
            level=level, data=message, logger=logger_name
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
    def session(self):
        """Access to the underlying session for advanced usage."""
        return self.request_context.session

    # Convenience methods for common log levels
    async def debug(self, message: str, logger_name: str | None = None) -> None:
        """Send a debug log message."""
        await self.log(level="debug", message=message, logger_name=logger_name)

    async def info(self, message: str, logger_name: str | None = None) -> None:
        """Send an info log message."""
        await self.log(level="info", message=message, logger_name=logger_name)

    async def warning(self, message: str, logger_name: str | None = None) -> None:
        """Send a warning log message."""
        await self.log(level="warning", message=message, logger_name=logger_name)

    async def error(self, message: str, logger_name: str | None = None) -> None:
        """Send an error log message."""
        await self.log(level="error", message=message, logger_name=logger_name)

    async def list_roots(self) -> list[Root]:
        """List the roots available to the server, as indicated by the client."""
        result = await self.request_context.session.list_roots()
        return result.roots

    async def sample(
        self,
        messages: str | list[str | SamplingMessage],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model_preferences: ModelPreferences | str | list[str] | None = None,
    ) -> TextContent | ImageContent:
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
        elif isinstance(messages, list):
            sampling_messages = [
                SamplingMessage(content=TextContent(text=m, type="text"), role="user")
                if isinstance(m, str)
                else m
                for m in messages
            ]

        result: CreateMessageResult = await self.request_context.session.create_message(
            messages=sampling_messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model_preferences=self._parse_model_preferences(model_preferences),
        )

        return result.content

    def get_http_request(self) -> Request:
        """Get the active starlette request."""

        # Deprecation warning, added in FastMCP 2.2.11
        warnings.warn(
            "Context.get_http_request() is deprecated and will be removed in a future version. "
            "Use get_http_request() from fastmcp.server.dependencies instead. "
            "See https://gofastmcp.com/patterns/http-requests for more details.",
            DeprecationWarning,
            stacklevel=2,
        )

        return fastmcp.server.dependencies.get_http_request()

    def _parse_model_preferences(
        self, model_preferences: ModelPreferences | str | list[str] | None
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
            return ModelPreferences(
                hints=[ModelHint(name=h) for h in model_preferences]
            )
        else:
            raise ValueError(
                "model_preferences must be one of: ModelPreferences, str, list[str], or None."
            )
