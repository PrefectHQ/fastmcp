"""SDK-seam middleware coverage (v4 D3, the middleware hybrid rebase).

Dispatch begins at the SDK seam, so ``on_message``/``on_request``/
``on_notification`` observe *every* inbound message — including the ones that
never reach a FastMCP handler (notifications, cancellations, and
malformed/unroutable requests) and were therefore invisible to FastMCP
middleware before. The typed per-method hooks keep firing exactly once, interior,
where ``call_next`` yields the typed component result.
"""

from typing import Any

import mcp_types
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult

from fastmcp import Client, FastMCP
from fastmcp.server.context import Context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


class SeamRecorder(Middleware):
    """Records ``(hook, method)`` before delegating, so a hook is captured even
    when ``call_next`` raises (a pre-handler failure or a guard-tool suspend)."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str | None]] = []

    async def on_message(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        self.records.append(("on_message", context.method))
        return await call_next(context)

    async def on_request(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        self.records.append(("on_request", context.method))
        return await call_next(context)

    async def on_notification(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        self.records.append(("on_notification", context.method))
        return await call_next(context)

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        self.records.append(("on_call_tool", context.method))
        return await call_next(context)


def _adder() -> FastMCP:
    server = FastMCP("SeamServer")

    @server.tool
    def add(a: int, b: int) -> int:
        return a + b

    return server


class TestNotificationVisibility:
    async def test_client_cancelled_notification_reaches_on_message(self):
        """A ``notifications/cancelled`` from the client is observed by
        ``on_message`` and ``on_notification`` — it never reaches a FastMCP
        handler, so before the rebase it was invisible to middleware."""
        server = _adder()
        recorder = SeamRecorder()
        server.add_middleware(recorder)

        async with Client(server) as client:
            await client.session.send_notification(
                mcp_types.CancelledNotification(
                    params=mcp_types.CancelledNotificationParams(
                        request_id="never-issued"
                    )
                )
            )
            # Round-trip on the same connection so the notification is dispatched
            # before we assert (in-order delivery).
            await client.call_tool("add", {"a": 1, "b": 2})

        assert ("on_message", "notifications/cancelled") in recorder.records
        assert ("on_notification", "notifications/cancelled") in recorder.records

    async def test_client_progress_notification_reaches_on_message(self):
        """A generic client notification is observed by ``on_message``."""
        server = _adder()
        recorder = SeamRecorder()
        server.add_middleware(recorder)

        async with Client(server) as client:
            await client.session.send_notification(
                mcp_types.ProgressNotification(
                    params=mcp_types.ProgressNotificationParams(
                        progress_token="tok", progress=1.0
                    )
                )
            )
            await client.call_tool("add", {"a": 1, "b": 2})

        assert ("on_message", "notifications/progress") in recorder.records


class TestUnroutableAndMalformed:
    async def test_unroutable_method_observed_by_on_message(self):
        """An unknown method fails routing before any handler; the seam still
        runs ``on_message``/``on_request`` around the failure."""
        server = _adder()
        recorder = SeamRecorder()
        server.add_middleware(recorder)

        async with Client(server) as client:
            with pytest.raises(MCPError):
                await client.session._dispatcher.send_raw_request(
                    "does/not/exist", {}, {}
                )

        assert ("on_message", "does/not/exist") in recorder.records
        assert ("on_request", "does/not/exist") in recorder.records

    async def test_malformed_component_params_observed_by_on_message(self):
        """A ``tools/call`` with malformed params fails validation before the
        interior handler runs, so no typed hook fires — but the seam observes the
        failure through ``on_message``, and ``on_call_tool`` does not fire."""
        server = _adder()
        recorder = SeamRecorder()
        server.add_middleware(recorder)

        async with Client(server) as client:
            with pytest.raises(MCPError):
                await client.session._dispatcher.send_raw_request(
                    "tools/call", {"not_a_valid": "param"}, {}
                )

        assert ("on_message", "tools/call") in recorder.records
        assert ("on_call_tool", "tools/call") not in recorder.records


class TestSingleFire:
    async def test_each_hook_fires_once_per_component_call(self):
        """One ``tools/call`` fires ``on_message`` once and ``on_call_tool`` once —
        the interior dispatch is the single entry for component methods; the seam
        does not double-run it."""
        server = _adder()
        recorder = SeamRecorder()
        server.add_middleware(recorder)

        async with Client(server) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        on_message = [r for r in recorder.records if r == ("on_message", "tools/call")]
        on_call_tool = [
            r for r in recorder.records if r == ("on_call_tool", "tools/call")
        ]
        assert len(on_message) == 1
        assert len(on_call_tool) == 1


def _guard_server() -> FastMCP:
    server = FastMCP("Guard")

    @server.tool
    async def guard(ctx: Context) -> str | InputRequiredResult:
        if ctx.input_responses is None:
            request = ElicitRequest(
                method="elicitation/create",
                params=ElicitRequestFormParams(
                    message="Your name?",
                    requested_schema={
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                ),
            )
            return InputRequiredResult(
                result_type="input_required",
                input_requests={"name": request},
                request_state=None,
            )
        return "done"

    return server


class TestSuspendVisibility:
    async def test_suspend_is_not_surfaced_as_a_result_to_component_hooks(self):
        """A guard-tool suspend travels as the internal ``ToolInputRequired``
        control signal (a ``BaseException``), so a component hook's ``call_next``
        raises rather than returning: the hook enters but never observes a result.
        The ``InputRequiredResult`` is produced at the wire boundary, not handed
        back up the FastMCP chain as a component result."""

        class SuspendProbe(Middleware):
            def __init__(self) -> None:
                self.entered = 0
                self.completed = 0

            async def on_call_tool(
                self, context: MiddlewareContext, call_next: CallNext
            ) -> Any:
                self.entered += 1
                result = await call_next(context)
                self.completed += 1
                return result

        server = _guard_server()
        probe = SuspendProbe()
        server.add_middleware(probe)

        async with Client(server, mode="auto") as client:
            result = await client.session.call_tool(
                "guard", {}, allow_input_required=True
            )

        assert isinstance(result, InputRequiredResult)
        assert probe.entered == 1
        # call_next raised the suspend signal, so the hook never saw a result.
        assert probe.completed == 0
