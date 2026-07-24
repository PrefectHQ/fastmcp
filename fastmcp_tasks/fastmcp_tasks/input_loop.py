"""The end-and-reenter capture wrapper for guard-pattern task tools.

A guard tool asks for input by *returning* an `InputRequiredResult` rather than
awaiting `ctx.elicit()`. Foreground, each such return is one leg of a
multi-round-trip: the tool returns, the client answers, the framework re-invokes
the tool with the answers on `ctx.input_responses`. The tool body is written
once and is oblivious to how many legs it takes.

As a background task the leg boundary is a *worker* boundary. This wrapper runs
the tool body exactly once. If the body returns a real value, it is the leg's
result. If the body returns an `InputRequiredResult`, the wrapper records the
leg's outstanding requests (and any carried `request_state`) to Redis and
returns — the Docket execution then completes and the worker is freed. The task
sits in `input_required` as durable state until the client answers via
`tasks/update`, which enqueues a fresh Docket execution (the next leg) that
re-runs this wrapper with the accumulated state injected onto `ctx`. No worker
is ever blocked awaiting input.

The wrapper preserves the wrapped callable's signature so Docket's dependency
injection still resolves the tool's parameters (its own args, `ctx`, and any
Docket-native dependencies) exactly as it would for the raw callable. The
per-leg state (`ctx.input_responses` / `ctx.request_state`) is injected by the
worker `Context` factory (`make_task_context`) before the body runs.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import TYPE_CHECKING, Any

import mcp_types

from fastmcp.exceptions import FastMCPError
from fastmcp.tools.base import InputRequiredToolResult, ToolResult
from fastmcp_tasks.context import get_task_context, get_task_leg_number
from fastmcp_tasks.input_store import store_outstanding

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from docket import Docket

logger = logging.getLogger(__name__)


def _as_input_required(result: Any) -> mcp_types.InputRequiredResult | None:
    """Return the `InputRequiredResult` a guard leg produced, or None.

    A tool body may return the bare `InputRequiredResult` or the
    `InputRequiredToolResult` wrapper FastMCP uses foreground; both mean the same
    ask.
    """
    if isinstance(result, InputRequiredToolResult):
        return result.input_required
    if isinstance(result, mcp_types.InputRequiredResult):
        return result
    return None


def _serialize_requests(
    input_requests: mcp_types.InputRequests,
) -> dict[str, dict[str, Any]]:
    """Dump each request to the wire payload surfaced for the client to answer."""
    return {
        key: request.model_dump(by_alias=True, mode="json", exclude_none=True)
        for key, request in input_requests.items()
    }


def _resolve_docket() -> Docket | None:
    """Resolve the active Docket from the current context or worker default."""
    from fastmcp.server.dependencies import get_context
    from fastmcp_tasks.dependencies import _current_docket

    try:
        docket = get_context().fastmcp._docket
    except RuntimeError:
        docket = None
    if docket is None:
        docket = _current_docket.get()
    return docket


def _mask_error_details() -> bool:
    """The worker server's error-masking policy, mirroring the sync call path.

    Resolves the owning server through ``get_server()`` (the worker-server
    resolver) rather than ``get_context()``: a tool that raises without ever
    requesting a ``ctx`` parameter has no active ``Context``, so reading the
    policy off the context would silently fall back to the global default and
    leak unmasked error text.
    """
    import fastmcp
    from fastmcp.server.dependencies import get_server

    try:
        return get_server()._mask_error_details
    except RuntimeError:
        return fastmcp.settings.mask_error_details


def _error_result(tool_name: str, exc: Exception) -> ToolResult:
    """An ``is_error`` result for a task tool that raised, mirroring foreground.

    A raised tool error is a *completed* task carrying an error result, never a
    ``failed`` task (SEP-2663 reserves ``failed`` for protocol faults, and a live
    ``tools/call`` returns the same `isError` result). A `FastMCPError` (e.g.
    ``ToolError``) reaches the client verbatim, as the synchronous path re-raises
    it unmasked; any other exception is masked per the server's policy.
    """
    if isinstance(exc, FastMCPError):
        message = str(exc)
    elif _mask_error_details():
        message = f"Error calling tool {tool_name!r}"
    else:
        message = f"Error calling tool {tool_name!r}: {exc}"
    return ToolResult(
        content=[mcp_types.TextContent(type="text", text=message)], is_error=True
    )


def reentrant_task_fn(
    fn: Callable[..., Awaitable[Any]],
    tool_name: str,
) -> Callable[..., Awaitable[Any]]:
    """Wrap a task tool's callable to capture a guard leg's ask (end-and-reenter).

    Signature-preserving, so Docket injects the wrapped callable's parameters
    unchanged. The body runs exactly once: a real return is the leg's result; an
    `InputRequiredResult` is captured to Redis (outstanding requests + carried
    state) and the wrapper returns, ending the leg without blocking. The next
    leg is enqueued by ``tasks/update`` when the client answers. A raised tool
    error becomes a completed `is_error` result (not a failed task), matching the
    synchronous `tools/call` path.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = await fn(*args, **kwargs)
        except FastMCPError as exc:
            return _error_result(tool_name, exc)
        except Exception as exc:
            logger.exception("background task tool %r raised", tool_name)
            return _error_result(tool_name, exc)
        input_required = _as_input_required(result)
        if input_required is None:
            return result

        requests = input_required.input_requests or {}
        request_state = input_required.request_state
        if not requests:
            if request_state is None:
                # Asks nothing and carries nothing — terminal, not a park.
                return result
            # State-only round: foreground re-invokes the tool after a backoff,
            # carrying `request_state` forward with no client interaction. The
            # tasked path has no self-continuation for that yet, so parking it
            # (with no requests for the client to answer) would strand the task.
            # Fail loudly rather than silently report a wrong completed result.
            return _error_result(
                tool_name,
                FastMCPError(
                    "A background task returned a state-only "
                    "InputRequiredResult (request_state with no input_requests). "
                    "Checkpoint-style rounds that carry state without asking the "
                    "client anything are not yet supported for tasks; include at "
                    "least one input request, or run the tool synchronously."
                ),
            )

        task_context = get_task_context()
        docket = _resolve_docket()
        if task_context is None or docket is None:
            logger.warning(
                "guard leg produced an ask outside a task worker; returning it"
            )
            return result

        await store_outstanding(
            docket,
            task_context.task_scope,
            task_context.task_id,
            get_task_leg_number(),
            _serialize_requests(requests),
            request_state,
        )
        # The leg ends here: the Docket execution completes and the worker is
        # freed. The task is now input_required until tasks/update enqueues the
        # next leg. Return None so the completed leg carries no stray result.
        return None

    # `functools.wraps` copies `__wrapped__`, so `inspect.signature` already
    # unwraps to `fn`; set it explicitly too, so a dependency injector reading
    # `__signature__` directly (rather than following `__wrapped__`) still sees
    # the tool's real parameters.
    wrapper.__signature__ = inspect.signature(fn)  # ty: ignore[unresolved-attribute]
    return wrapper
