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
    runtime_checkable,
)

import mcp.types as mt
from mcp.server.lowlevel.helper_types import ReadResourceContents
from typing_extensions import TypeVar

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.tool import Tool, ToolResult

if TYPE_CHECKING:
    from fastmcp.server.context import Context

__all__ = [
    "Middleware",
    "MiddlewareContext",
    "CallNext",
]

logger = logging.getLogger(__name__)


T = TypeVar("T", default=Any)
R = TypeVar("R", covariant=True, default=Any)


@runtime_checkable
class CallNext(Protocol[T, R]):
    def __call__(self, context: MiddlewareContext[T]) -> Awaitable[R]: ...


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
    middleware: Middleware, call_next: CallNext[T, R]
) -> CallNext[T, R]:
    """Create a wrapper that applies a single middleware to a context. The
    closure bakes in the middleware and call_next function, so it can be
    passed to other functions that expect a call_next function."""

    async def wrapper(context: MiddlewareContext[T]) -> R:
        return await middleware(context, call_next)

    return wrapper


class Middleware:
    """Base class for FastMCP middleware with dispatching hooks."""

    async def __call__(
        self,
        context: MiddlewareContext[T],
        call_next: CallNext[T, Any],
    ) -> Any:
        """Main entry point that orchestrates the pipeline."""
        handler_chain = await self._dispatch_handler(
            context,
            call_next=call_next,
        )
        return await handler_chain(context)

    async def _dispatch_handler(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> CallNext[Any, Any]:
        """Builds a chain of handlers for a given message."""
        handler = call_next

        match context.method:
            case "tools/call":
                handler = partial(self.on_call_tool, call_next=handler)
            case "resources/read":
                handler = partial(self.on_read_resource, call_next=handler)
            case "prompts/get":
                handler = partial(self.on_get_prompt, call_next=handler)
            case "tools/list":
                handler = partial(self.on_list_tools, call_next=handler)
            case "resources/list":
                handler = partial(self.on_list_resources, call_next=handler)
            case "resources/templates/list":
                handler = partial(self.on_list_resource_templates, call_next=handler)
            case "prompts/list":
                handler = partial(self.on_list_prompts, call_next=handler)

        match context.type:
            case "request":
                handler = partial(self.on_request, call_next=handler)
            case "notification":
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
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        return await call_next(context)

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, list[ReadResourceContents]],
    ) -> list[ReadResourceContents]:
        return await call_next(context)

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, mt.GetPromptResult],
    ) -> mt.GetPromptResult:
        return await call_next(context)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, list[Tool]],
    ) -> list[Tool]:
        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, list[Resource]],
    ) -> list[Resource]:
        return await call_next(context)

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[mt.ListResourceTemplatesRequest, list[ResourceTemplate]],
    ) -> list[ResourceTemplate]:
        return await call_next(context)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, list[Prompt]],
    ) -> list[Prompt]:
        return await call_next(context)
