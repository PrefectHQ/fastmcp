"""Middleware message visibility (v4 D3, the middleware hybrid rebase).

Dispatch begins in the SDK's middleware layer, so ``on_message``/``on_request``/
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
from fastmcp.tools.base import InputRequiredToolResult


class HookRecorder(Middleware):
    """Records ``(hook, method)`` before delegating, so a hook is captured even
    when ``call_next`` raises (a pre-handler failure)."""

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

    async def on_list_tools(
        self, context: MiddlewareContext, call_next: CallNext
    ) -> Any:
        self.records.append(("on_list_tools", context.method))
        return await call_next(context)


def _adder() -> FastMCP:
    server = FastMCP("AdderServer")

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
        recorder = HookRecorder()
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
        recorder = HookRecorder()
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
        """An unknown method fails routing before any handler; the root dispatch still
        runs ``on_message``/``on_request`` around the failure."""
        server = _adder()
        recorder = HookRecorder()
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
        interior handler runs, so no typed hook fires — but the root dispatch observes the
        failure through ``on_message``, and ``on_call_tool`` does not fire."""
        server = _adder()
        recorder = HookRecorder()
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
        the interior dispatch is the single entry for component methods; the root dispatch
        does not double-run it."""
        server = _adder()
        recorder = HookRecorder()
        server.add_middleware(recorder)

        async with Client(server) as client:
            await client.call_tool("add", {"a": 1, "b": 2})

        on_message = [r for r in recorder.records if r == ("on_message", "tools/call")]
        on_call_tool = [
            r for r in recorder.records if r == ("on_call_tool", "tools/call")
        ]
        assert len(on_message) == 1
        assert len(on_call_tool) == 1


class TestRawMiddlewareCompatibility:
    """Middleware may override ``__call__(context, call_next)`` — the documented
    raw signature. The dispatch phase travels out-of-band, so that contract is
    unchanged and such middleware keeps working."""

    async def test_raw_call_override_still_works(self):
        seen: list[str | None] = []

        class RawMiddleware(Middleware):
            async def __call__(self, context, call_next):
                seen.append(context.method)
                return await call_next(context)

        server = _adder()
        server.add_middleware(RawMiddleware())

        async with Client(server) as client:
            result = await client.call_tool("add", {"a": 1, "b": 2})
            await client.session.send_notification(
                mcp_types.ProgressNotification(
                    params=mcp_types.ProgressNotificationParams(
                        progress_token="tok", progress=1.0
                    )
                )
            )
            await client.call_tool("add", {"a": 1, "b": 2})

        assert result.data == 3
        # It observes both a component call and a message the root dispatch owns.
        assert "tools/call" in seen
        assert "notifications/progress" in seen


class TestMessageModification:
    """The root dispatch hands middleware a copy of the raw params, so edits made
    through the documented inspect/modify contract must be folded back into the
    SDK context before the real dispatch runs."""

    async def test_modified_message_reaches_sdk_dispatch(self):
        """A ``logging/setLevel`` carrying an invalid level fails params
        validation inside ``call_next``. Middleware that rewrites the message to
        a valid level makes the request succeed — which only happens if the edit
        is actually forwarded."""

        class RewriteLevel(Middleware):
            async def on_message(self, context, call_next):
                if context.method == "logging/setLevel":
                    context.message["level"] = "debug"
                return await call_next(context)

        server = _adder()
        server.add_middleware(RewriteLevel())

        async with Client(server) as client:
            await client.session._dispatcher.send_raw_request(
                "logging/setLevel", {"level": "not-a-valid-level"}, {}
            )

    async def test_unmodified_message_dispatches_unchanged(self):
        """An observation-only hook leaves dispatch untouched."""
        server = _adder()
        recorder = HookRecorder()
        server.add_middleware(recorder)

        async with Client(server) as client:
            await client.session._dispatcher.send_raw_request(
                "logging/setLevel", {"level": "debug"}, {}
            )

        assert ("on_message", "logging/setLevel") in recorder.records

    async def test_method_rewrite_does_not_redirect_dispatch(self):
        """Only the message is rewritable. Dispatch has already branched on the
        method to decide this message has no interior handler, so honoring a
        rewrite into a component method would hand it to a handler that runs the
        chain again — firing the generic hooks twice for one message. The
        rewrite is ignored and the invariant holds."""

        class RewriteMethod(Middleware):
            async def on_message(self, context, call_next):
                if context.method == "ping":
                    return await call_next(context.copy(method="tools/list"))
                return await call_next(context)

        server = _adder()
        recorder = HookRecorder()
        # Recorder outermost, so it observes the message as it arrived; the
        # rewriter runs inside it.
        server.add_middleware(recorder)
        server.add_middleware(RewriteMethod())

        async with Client(server) as client:
            await client.session._dispatcher.send_raw_request("ping", {}, {})

        # Had the rewrite redirected dispatch, the component handler would have
        # run the chain again — a second on_message, plus an on_list_tools for a
        # request that was never a tools/list.
        assert [r for r in recorder.records if r == ("on_message", "ping")] == [
            ("on_message", "ping")
        ]
        assert not [r for r in recorder.records if r == ("on_message", "tools/list")]
        assert not [r for r in recorder.records if r[0] == "on_list_tools"]

    async def test_failed_component_request_is_observed_not_retried(self):
        """A component request that dies in validation reaches the hooks as a
        failure. A hook cannot repair it from here: re-dispatching would run the
        handler and fire the generic hooks a second time, so the failure stands
        and ``on_message`` sees it exactly once."""

        class RepairAttempt(Middleware):
            async def on_message(self, context, call_next):
                if context.method == "tools/call":
                    context.message["name"] = "add"
                    context.message["arguments"] = {"a": 1, "b": 2}
                return await call_next(context)

        server = _adder()
        recorder = HookRecorder()
        server.add_middleware(RepairAttempt())
        server.add_middleware(recorder)

        async with Client(server) as client:
            with pytest.raises(MCPError):
                await client.session._dispatcher.send_raw_request(
                    "tools/call", {"not_a_valid": "param"}, {}
                )

        calls = [r for r in recorder.records if r == ("on_message", "tools/call")]
        assert len(calls) == 1
        assert ("on_call_tool", "tools/call") not in recorder.records


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


class TestAskVisibility:
    async def test_ask_is_the_observed_result_of_a_guard_leg(self):
        """Each MRTR leg is a complete request→response cycle: a guard tool's ask
        is the full, legitimate result of that leg. A component hook's
        ``call_next`` returns it as an ordinary value — an
        ``InputRequiredToolResult`` (a ``ToolResult`` subclass) — so the hook
        completes normally and can identify the ask by ``isinstance``."""

        class AskProbe(Middleware):
            def __init__(self) -> None:
                self.entered = 0
                self.results: list[Any] = []

            async def on_call_tool(
                self, context: MiddlewareContext, call_next: CallNext
            ) -> Any:
                self.entered += 1
                result = await call_next(context)
                self.results.append(result)
                return result

        server = _guard_server()
        probe = AskProbe()
        server.add_middleware(probe)

        async with Client(server, mode="auto") as client:
            result = await client.session.call_tool(
                "guard", {}, allow_input_required=True
            )

        assert isinstance(result, InputRequiredResult)
        assert probe.entered == 1
        # The hook completed and observed the ask as the leg's result value.
        assert len(probe.results) == 1
        assert isinstance(probe.results[0], InputRequiredToolResult)

    async def test_hooks_fire_once_per_round_across_a_continuation(self):
        """The fires-once invariant holds across a continuation — the one place
        root dispatch and MRTR genuinely meet. Each round is its own complete
        request→response cycle, so answering the ask runs the chain a second
        time in full rather than double-firing on either round."""
        server = _guard_server()
        recorder = HookRecorder()
        server.add_middleware(recorder)

        async with Client(server, mode="auto") as client:
            ask = await client.session.call_tool("guard", {}, allow_input_required=True)
            assert isinstance(ask, InputRequiredResult)

            answered = await client.session.call_tool(
                "guard",
                {},
                input_responses={
                    "name": {"action": "accept", "content": {"name": "Ada"}}
                },
                request_state=ask.request_state,
                allow_input_required=True,
            )

        assert isinstance(answered, mcp_types.CallToolResult)
        # Two rounds — the ask and the answer — and exactly one chain per round.
        on_message = [r for r in recorder.records if r == ("on_message", "tools/call")]
        on_call_tool = [
            r for r in recorder.records if r == ("on_call_tool", "tools/call")
        ]
        assert len(on_message) == 2
        assert len(on_call_tool) == 2


class TestSchedulingProbe:
    async def test_trivial_noop(self):
        """Temporary probe: does merely adding a 7th test destabilize the run?"""
        assert True
