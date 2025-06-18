from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    Protocol,
    TypeVar,
    runtime_checkable,
)

import mcp.types as mt

if TYPE_CHECKING:
    from fastmcp.server.context import Context

logger = logging.getLogger(__name__)


T = TypeVar("T")
R = TypeVar("R", covariant=True)


@runtime_checkable
class CallNext(Protocol[T, R]):
    def __call__(self, context: MiddlewareContext[T]) -> Awaitable[R]: ...


ServerResultT = TypeVar(
    "ServerResultT",
    bound=mt.EmptyResult
    | mt.InitializeResult
    | mt.CompleteResult
    | mt.GetPromptResult
    | mt.ListPromptsResult
    | mt.ListResourcesResult
    | mt.ListResourceTemplatesResult
    | mt.ReadResourceResult
    | mt.CallToolResult
    | mt.ListToolsResult,
)


@runtime_checkable
class ServerResultProtocol(Protocol[ServerResultT]):
    root: ServerResultT


@dataclass(kw_only=True, frozen=True)
class MiddlewareContext(Generic[T]):
    """
    Unified context for all middleware operations.
    """

    message: T

    fastmcp_context: Context | None = None

    # Common metadata
    source: Literal["client", "server"] = "client"
    type: Literal["request", "notification"] = "request"
    method: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def copy(self, **kwargs: Any) -> MiddlewareContext[T]:
        return replace(self, **kwargs)


def make_middleware_wrapper(
    middleware: MCPMiddleware, call_next: CallNext[T, R]
) -> CallNext[T, R]:
    """Create a wrapper that applies a single middleware to a context. The
    closure bakes in the middleware and call_next function, so it can be
    passed to other functions that expect a call_next function."""

    async def wrapper(context: MiddlewareContext[T]) -> R:
        return await middleware(context, call_next)

    return wrapper


class MCPMiddleware:
    """Base class for FastMCP middleware with dispatching hooks."""

    async def __call__(
        self,
        context: MiddlewareContext[T],
        call_next: CallNext[T, Any],
    ) -> Any:
        """Main entry point that orchestrates the pipeline."""
        handler_chain = await self._dispatch_handler(
            context.message,
            call_next=call_next,
        )
        return await handler_chain(context)

    async def _dispatch_handler(
        self, message: Any, call_next: CallNext[Any, Any]
    ) -> CallNext[Any, Any]:
        """Builds a chain of handlers for a given message."""
        handler = call_next

        match message:
            case mt.CallToolRequest():
                handler = partial(self.on_call_tool, call_next=handler)
            case mt.ReadResourceRequest():
                handler = partial(self.on_read_resource, call_next=handler)
            case mt.GetPromptRequest():
                handler = partial(self.on_get_prompt, call_next=handler)

        match message:
            case mt.Request():
                handler = partial(self.on_request, call_next=handler)
            case mt.Notification():
                handler = partial(self.on_notification, call_next=handler)

        handler = partial(self.on_message, call_next=handler)

        return handler

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        return await call_next(context)

    async def on_request(
        self,
        context: MiddlewareContext[mt.Request],
        call_next: CallNext[mt.Request, Any],
    ) -> Any:
        return await call_next(context)

    async def on_notification(
        self,
        context: MiddlewareContext[mt.Notification],
        call_next: CallNext[mt.Notification, Any],
    ) -> Any:
        return await call_next(context)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequest],
        call_next: CallNext[mt.CallToolRequest, mt.CallToolResult],
    ) -> mt.CallToolResult:
        return await call_next(context)

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequest],
        call_next: CallNext[mt.ReadResourceRequest, mt.ReadResourceResult],
    ) -> mt.ReadResourceResult:
        return await call_next(context)

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequest],
        call_next: CallNext[mt.GetPromptRequest, mt.GetPromptResult],
    ) -> mt.GetPromptResult:
        return await call_next(context)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, mt.ListToolsResult],
    ) -> mt.ListToolsResult:
        return await call_next(context)
