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

from fastmcp.prompts.prompt import Prompt
from fastmcp.resources.resource import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.tool import Tool

if TYPE_CHECKING:
    from fastmcp.server.context import Context

__all__ = [
    "Middleware",
    "MiddlewareContext",
    "CallNext",
    "ListToolsResult",
    "ListResourcesResult",
    "ListResourceTemplatesResult",
    "ListPromptsResult",
]

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


@dataclass(kw_only=True)
class ListToolsResult:
    tools: dict[str, Tool]


@dataclass(kw_only=True)
class ListResourcesResult:
    resources: list[Resource]


@dataclass(kw_only=True)
class ListResourceTemplatesResult:
    resource_templates: list[ResourceTemplate]


@dataclass(kw_only=True)
class ListPromptsResult:
    prompts: list[Prompt]


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
        call_next: CallNext[mt.CallToolRequestParams, mt.CallToolResult],
    ) -> mt.CallToolResult:
        return await call_next(context)

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, mt.ReadResourceResult],
    ) -> mt.ReadResourceResult:
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
        call_next: CallNext[mt.ListToolsRequest, ListToolsResult],
    ) -> ListToolsResult:
        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, ListResourcesResult],
    ) -> ListResourcesResult:
        return await call_next(context)

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[
            mt.ListResourceTemplatesRequest, ListResourceTemplatesResult
        ],
    ) -> ListResourceTemplatesResult:
        return await call_next(context)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, ListPromptsResult],
    ) -> ListPromptsResult:
        return await call_next(context)
