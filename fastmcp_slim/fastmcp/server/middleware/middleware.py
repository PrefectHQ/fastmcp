from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    Protocol,
    runtime_checkable,
)

import mcp_types as mt
from typing_extensions import TypeVar

from fastmcp.prompts.base import Prompt, PromptResult
from fastmcp.resources.base import Resource, ResourceResult
from fastmcp.resources.template import ResourceTemplate
from fastmcp.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from fastmcp.server.context import Context

__all__ = [
    "CallNext",
    "Middleware",
    "MiddlewareContext",
]

logger = logging.getLogger(__name__)

MiddlewarePhase = Literal["all", "outer", "typed"]
"""Which slice of a middleware's hooks to run in a single dispatch pass.

- ``"all"`` runs the whole chain in one pass (``on_message`` -> ``on_request`` /
  ``on_notification`` -> the typed per-method hook). This is what the interior
  component methods (``call_tool``, ``list_tools``, ...) run for the methods they
  serve, and what the ``initialize`` request runs at the dispatch root.
- ``"outer"`` runs only ``on_message`` and ``on_request``/``on_notification``.
  The root dispatch (in the SDK's middleware layer) runs this pass for the messages the interior never
  dispatches (notifications, cancellations, unroutable/non-component requests,
  and pre-handler failures), so ``on_message`` observes *every* inbound message
  without double-firing for the component methods the interior already covers.
- ``"typed"`` runs only the per-method hook. Reserved for a future full split;
  no current dispatch path uses it.
"""


_interior_dispatched: ContextVar[bool] = ContextVar(
    "fastmcp_interior_dispatched", default=False
)
"""Set to True by an interior component dispatch when it runs its middleware chain.

The root dispatch reads this to tell whether the FastMCP middleware
chain already fired *inside* the wire request (so ``on_message``/``on_request``
were observed there — including any tool exception, exactly where the built-in
error/logging/timing middleware expect them). It is only consulted for the
component methods: if such a request fails *before* the interior runs (malformed
params, routing), the flag stays False and the root dispatch observes the failure itself.
"""


def mark_interior_dispatched() -> None:
    """Record that an interior component middleware chain ran for this message."""
    _interior_dispatched.set(True)


_dispatch_phase: ContextVar[MiddlewarePhase] = ContextVar(
    "fastmcp_dispatch_phase", default="all"
)
"""The dispatch phase for the middleware chain currently running.

Set by ``FastMCP._run_middleware`` around each chain execution and read by
``Middleware.__call__``, so the phase never appears in the middleware call
signature — user middleware overriding the documented
``__call__(context, call_next)`` keeps working unchanged.
"""


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


def make_handler_wrapper(
    handler: Callable[..., Awaitable[Any]],
    call_next: CallNext[Any, Any],
) -> CallNext[Any, Any]:
    async def wrapper(context: MiddlewareContext[Any]) -> Any:
        return await handler(context, call_next=call_next)

    return wrapper


class Middleware:
    """Base class for FastMCP middleware with dispatching hooks."""

    async def __call__(
        self,
        context: MiddlewareContext[T],
        call_next: CallNext[T, Any],
    ) -> Any:
        """Main entry point that orchestrates the pipeline.

        The dispatch phase — which slice of the hooks runs (see
        ``MiddlewarePhase``) — is read from ``_dispatch_phase`` rather than
        passed as an argument, so middleware that overrides this method with the
        documented ``(context, call_next)`` signature keeps working unchanged.
        Such an override runs once per message regardless of phase, which
        matches its pre-existing behavior.
        """
        handler_chain = await self._dispatch_handler(
            context,
            call_next=call_next,
            phase=_dispatch_phase.get(),
        )
        return await handler_chain(context)

    async def _dispatch_handler(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
        phase: MiddlewarePhase = "all",
    ) -> CallNext[Any, Any]:
        """Builds a chain of handlers for a given message and dispatch phase."""
        handler = call_next

        if phase in ("all", "typed"):
            match context.method:
                case "initialize":
                    handler = make_handler_wrapper(self.on_initialize, handler)
                case "tools/call":
                    handler = make_handler_wrapper(self.on_call_tool, handler)
                case "resources/read":
                    handler = make_handler_wrapper(self.on_read_resource, handler)
                case "prompts/get":
                    handler = make_handler_wrapper(self.on_get_prompt, handler)
                case "tools/list":
                    handler = make_handler_wrapper(self.on_list_tools, handler)
                case "resources/list":
                    handler = make_handler_wrapper(self.on_list_resources, handler)
                case "resources/templates/list":
                    handler = make_handler_wrapper(
                        self.on_list_resource_templates,
                        handler,
                    )
                case "prompts/list":
                    handler = make_handler_wrapper(self.on_list_prompts, handler)

        if phase in ("all", "outer"):
            match context.type:
                case "request":
                    handler = make_handler_wrapper(self.on_request, handler)
                case "notification":
                    handler = make_handler_wrapper(self.on_notification, handler)

            handler = make_handler_wrapper(self.on_message, handler)

        return handler

    async def on_message(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        return await call_next(context)

    async def on_request(
        self,
        context: MiddlewareContext[mt.Request[Any, Any]],
        call_next: CallNext[mt.Request[Any, Any], Any],
    ) -> Any:
        return await call_next(context)

    async def on_notification(
        self,
        context: MiddlewareContext[mt.Notification[Any, Any]],
        call_next: CallNext[mt.Notification[Any, Any], Any],
    ) -> Any:
        return await call_next(context)

    async def on_initialize(
        self,
        context: MiddlewareContext[mt.InitializeRequest],
        call_next: CallNext[mt.InitializeRequest, mt.InitializeResult | None],
    ) -> mt.InitializeResult | None:
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
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        return await call_next(context)

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        return await call_next(context)

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        return await call_next(context)

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[
            mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]
        ],
    ) -> Sequence[ResourceTemplate]:
        return await call_next(context)

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        return await call_next(context)
