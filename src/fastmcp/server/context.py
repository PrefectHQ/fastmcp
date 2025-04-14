from __future__ import annotations as _annotations

from typing import Any, Generic, Literal

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.session import ServerSessionT
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import (
    CreateMessageResult,
    ImageContent,
    Root,
    SamplingMessage,
    TextContent,
)
from pydantic import BaseModel
from pydantic.networks import AnyUrl

from fastmcp.server.server import FastMCP
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)


class Context(BaseModel, Generic[ServerSessionT, LifespanContextT]):
    """Context object providing access to MCP capabilities.

    This provides a cleaner interface to MCP's RequestContext functionality.
    It gets injected into tool and resource functions that request it via type hints.

    To use context in a tool function, add a parameter with the Context type annotation:

    ```python
    @server.tool()
    def my_tool(x: int, ctx: Context) -> str:
        # Log messages to the client
        ctx.info(f"Processing {x}")
        ctx.debug("Debug info")
        ctx.warning("Warning message")
        ctx.error("Error message")

        # Report progress
        ctx.report_progress(50, 100)

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

    _request_context: RequestContext[ServerSessionT, LifespanContextT] | None
    _fastmcp: FastMCP | None

    def __init__(
        self,
        *,
        request_context: RequestContext[ServerSessionT, LifespanContextT] | None = None,
        fastmcp: FastMCP | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._request_context = request_context
        self._fastmcp = fastmcp

    @property
    def fastmcp(self) -> FastMCP:
        """Access to the FastMCP server."""
        if self._fastmcp is None:
            raise ValueError("Context is not available outside of a request")
        return self._fastmcp

    @property
    def request_context(self) -> RequestContext[ServerSessionT, LifespanContextT]:
        """Access to the underlying request context."""
        if self._request_context is None:
            raise ValueError("Context is not available outside of a request")
        return self._request_context

    async def report_progress(
        self, progress: float, total: float | None = None
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
            progress_token=progress_token, progress=progress, total=total
        )

    async def read_resource(self, uri: str | AnyUrl) -> list[ReadResourceContents]:
        """Read a resource by URI.

        Args:
            uri: Resource URI to read

        Returns:
            The resource content as either text or bytes
        """
        assert self._fastmcp is not None, (
            "Context is not available outside of a request"
        )
        return await self._fastmcp._mcp_read_resource(uri)

    async def log(
        self,
        level: Literal["debug", "info", "warning", "error"],
        message: str,
        *,
        logger_name: str | None = None,
    ) -> None:
        """Send a log message to the client.

        Args:
            level: Log level (debug, info, warning, error)
            message: Log message
            logger_name: Optional logger name
            **extra: Additional structured data to include
        """
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
    async def debug(self, message: str, **extra: Any) -> None:
        """Send a debug log message."""
        await self.log("debug", message, **extra)

    async def info(self, message: str, **extra: Any) -> None:
        """Send an info log message."""
        await self.log("info", message, **extra)

    async def warning(self, message: str, **extra: Any) -> None:
        """Send a warning log message."""
        await self.log("warning", message, **extra)

    async def error(self, message: str, **extra: Any) -> None:
        """Send an error log message."""
        await self.log("error", message, **extra)

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
        )

        return result.content
