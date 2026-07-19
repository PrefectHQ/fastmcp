"""Server-side guard-mode multi-round-trip (MRTR, SEP-2322).

A FastMCP tool may *suspend* by returning an ``InputRequiredResult``: the call
pauses, the client fulfils the embedded requests (elicitation / sampling /
roots), and the same tool re-runs with the answers on ``ctx.input_responses``
and the echoed opaque ``ctx.request_state``. This is the SDK's own base "guard"
model — the tool re-runs per round and checks whether the client's answers are
present — with FastMCP mirroring its semantics exactly.

These tests exercise the *emission* side (a FastMCP server producing the
``InputRequiredResult`` and being driven to completion) over both in-memory and
HTTP transports, plus the request-state boundary (framework-owned sealing) and
the ≤2025-11-25 era gate. The client-side *answering* path is covered by
``tests/client/client/test_input_required_driver.py``.
"""

from __future__ import annotations

from typing import Annotated

import mcp_types
import pytest
from mcp.client._input_required import InputRequiredRoundsExceededError
from mcp.server.request_state import RequestStateSecurity
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitRequest, InputRequiredResult
from pydantic import Field

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.middleware import Middleware
from fastmcp.utilities.tests import run_server_async


def _elicit(key: str, message: str, field: str) -> ElicitRequest:
    """A single-field form elicitation request keyed by ``key``."""
    params = mcp_types.ElicitRequestFormParams(
        message=message,
        requested_schema={
            "type": "object",
            "properties": {field: {"type": "string"}},
            "required": [field],
        },
    )
    return ElicitRequest(method="elicitation/create", params=params)


def _ask(
    request: ElicitRequest, key: str, request_state: str | None
) -> InputRequiredResult:
    return InputRequiredResult(
        result_type="input_required",
        input_requests={key: request},
        request_state=request_state,
    )


def _accepted(responses: mcp_types.InputResponses, key: str) -> dict[str, object]:
    """The accepted form content for one answered elicitation.

    ``ctx.input_responses`` values are the raw SDK union
    (``ElicitResult | CreateMessageResult | ListRootsResult``); a guard tool
    narrows to the response type it asked for.
    """
    answer = responses[key]
    assert isinstance(answer, mcp_types.ElicitResult)
    assert answer.content is not None
    return dict(answer.content)


def two_question_server(**server_kwargs) -> FastMCP:
    """A guard tool that asks two dependent questions across three rounds.

    Round 1 (no responses): ask for a destination.
    Round 2 (destination answered): ask for a date, carrying the destination
        forward through ``request_state`` (a computed value, not re-derived).
    Round 3 (date answered): read the carried destination out of
        ``request_state`` and finish.
    """
    mcp = FastMCP("guard", **server_kwargs)

    @mcp.tool
    async def book_flight(ctx: Context) -> str | InputRequiredResult:
        responses = ctx.input_responses
        if responses is None:
            return _ask(
                _elicit("destination", "Where would you like to fly?", "destination"),
                "destination",
                request_state=None,
            )
        if "destination" in responses:
            destination = _accepted(responses, "destination")["destination"]
            return _ask(
                _elicit("date", f"When to {destination}?", "date"),
                "date",
                request_state=f"dest={destination}",
            )
        assert ctx.request_state is not None
        destination = ctx.request_state.split("=", 1)[1]
        date = _accepted(responses, "date")["date"]
        return f"Booked {destination} on {date}"

    return mcp


def _two_answer_handler(asked: list[str]):
    async def handler(message, response_type, params, ctx):
        asked.append(message)
        if "Where" in message:
            return ElicitResult(
                action="accept", content=response_type(destination="Paris")
            )
        return ElicitResult(action="accept", content=response_type(date="2026-08-01"))

    return handler


class TestContextProperties:
    """`ctx.input_responses` / `ctx.request_state` thin passthroughs."""

    async def test_none_outside_mrtr_round(self):
        """On a plain call (no prior InputRequiredResult), both are None."""
        seen: dict[str, object] = {}
        mcp = FastMCP("x")

        @mcp.tool
        async def probe(ctx: Context) -> str:
            seen["input_responses"] = ctx.input_responses
            seen["request_state"] = ctx.request_state
            return "ok"

        async with Client(mcp, mode="auto") as client:
            await client.call_tool("probe", {})

        assert seen["input_responses"] is None
        assert seen["request_state"] is None

    async def test_none_without_request_context(self):
        """Off-request (no bound wire request) the properties are None, not a crash."""
        mcp = FastMCP("x")
        async with Context(fastmcp=mcp) as ctx:
            assert ctx.input_responses is None
            assert ctx.request_state is None


class TestOutputSchema:
    """An `InputRequiredResult` return arm is control flow, not output data, so
    it is stripped from output-schema derivation."""

    def test_union_arm_stripped_keeps_data_schema(self):
        from fastmcp.tools.function_tool import FunctionTool

        def book(x: int) -> str | InputRequiredResult:
            return "ok"

        tool = FunctionTool.from_function(book)
        assert tool.output_schema is not None
        # Schema is derived from the residual `str` arm (wrapped as {"result": ...}).
        assert tool.output_schema.get("x-fastmcp-wrap-result") is True

    def test_bare_input_required_return_suppresses_schema(self):
        from fastmcp.tools.function_tool import FunctionTool

        def suspend_only(x: int) -> InputRequiredResult:
            raise NotImplementedError

        tool = FunctionTool.from_function(suspend_only)
        assert tool.output_schema is None


class TestInMemoryLoop:
    async def test_two_question_loop_completes(self):
        """Two dependent asks complete over the in-memory transport; the
        client's elicitation handler answers both rounds."""
        asked: list[str] = []
        async with Client(
            two_question_server(),
            mode="auto",
            elicitation_handler=_two_answer_handler(asked),
        ) as client:
            assert client.protocol_version == "2026-07-28"
            result = await client.call_tool("book_flight", {})

        assert asked == ["Where would you like to fly?", "When to Paris?"]
        assert result.data == "Booked Paris on 2026-08-01"

    async def test_input_responses_visible_to_tool(self):
        """A guard tool reads the client's answer out of ctx.input_responses."""
        seen: list[object] = []
        mcp = FastMCP("echo")

        @mcp.tool
        async def ask_once(ctx: Context) -> str | InputRequiredResult:
            responses = ctx.input_responses
            if responses is None:
                return _ask(_elicit("name", "Your name?", "name"), "name", None)
            content = _accepted(responses, "name")
            seen.append(content)
            return f"Hi {content['name']}"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content=response_type(name="Ada"))

        async with Client(mcp, mode="auto", elicitation_handler=handler) as client:
            result = await client.call_tool("ask_once", {})

        assert seen == [{"name": "Ada"}]
        assert result.data == "Hi Ada"

    async def test_declined_answer_shape(self):
        """A client that declines delivers an ElicitResult with action='decline'
        and no content into ctx.input_responses (not an error)."""
        mcp = FastMCP("decline")

        @mcp.tool
        async def ask(ctx: Context) -> str | InputRequiredResult:
            responses = ctx.input_responses
            if responses is None:
                return _ask(_elicit("x", "q", "x"), "x", None)
            answer = responses["x"]
            assert isinstance(answer, mcp_types.ElicitResult)
            return f"action={answer.action} content={answer.content}"

        async def decline_handler(message, response_type, params, ctx):
            return ElicitResult(action="decline", content=None)

        async with Client(
            mcp, mode="auto", elicitation_handler=decline_handler
        ) as client:
            result = await client.call_tool("ask", {})

        assert result.data == "action=decline content=None"

    async def test_max_rounds_exceeded_raises(self):
        """A two-round guard under max_rounds=1 raises on the client driver."""
        asked: list[str] = []
        async with Client(
            two_question_server(),
            mode="auto",
            elicitation_handler=_two_answer_handler(asked),
            input_required_max_rounds=1,
        ) as client:
            with pytest.raises(InputRequiredRoundsExceededError):
                await client.call_tool("book_flight", {})


class TestMountedServer:
    async def test_guard_tool_through_parent(self):
        """A guard tool on a mounted child completes when called through the
        parent's namespaced name — ToolInputRequired propagates through the
        provider delegation and is sealed/returned at the parent's wire seam."""
        parent = FastMCP("parent")
        parent.mount(two_question_server(), namespace="sub")

        asked: list[str] = []
        async with Client(
            parent, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("sub_book_flight", {})

        assert asked == ["Where would you like to fly?", "When to Paris?"]
        assert result.data == "Booked Paris on 2026-08-01"


class TestEraGate:
    async def test_legacy_connection_rejects_with_era_error(self):
        """Returning an InputRequiredResult on a ≤2025-11-25 connection produces
        a clear era error naming 2026-07-28, not a generic 'invalid result'."""
        async with Client(two_question_server(), mode="legacy") as client:
            assert client.protocol_version == "2025-11-25"
            with pytest.raises(MCPError) as excinfo:
                await client.call_tool("book_flight", {})

        message = str(excinfo.value)
        assert "2026-07-28" in message
        assert "2025-11-25" in message
        assert "InputRequiredResult" in message


class TestRequestStateSealing:
    """The framework (SDK RequestStateBoundary middleware) owns sealing: a tool
    only ever mints/reads plaintext, and the wire value is authenticated."""

    def _one_shot_server(self) -> FastMCP:
        mcp = FastMCP("seal")

        @mcp.tool
        async def guard(ctx: Context) -> str | InputRequiredResult:
            if ctx.input_responses is None:
                return _ask(
                    _elicit("x", "q", "x"), "x", request_state="PLAINTEXT-STATE"
                )
            return f"state={ctx.request_state}"

        return mcp

    async def test_wire_request_state_is_sealed(self):
        """The requestState the client receives is the sealed token, never the
        plaintext the tool minted."""
        async with Client(self._one_shot_server(), mode="auto") as client:
            first = await client.session.call_tool(
                "guard", {}, allow_input_required=True
            )
        assert isinstance(first, InputRequiredResult)
        assert first.request_state is not None
        assert first.request_state != "PLAINTEXT-STATE"
        # The SDK's AES-256-GCM codec stamps a versioned "v1." prefix.
        assert first.request_state.startswith("v1.")

    async def test_tampered_request_state_rejected(self):
        """A modified requestState echo fails the boundary's integrity check with
        the frozen wire error, before the tool runs."""
        async with Client(self._one_shot_server(), mode="auto") as client:
            first = await client.session.call_tool(
                "guard", {}, allow_input_required=True
            )
            assert isinstance(first, InputRequiredResult)
            assert first.request_state is not None
            tampered = first.request_state[:-2] + (
                "AA" if not first.request_state.endswith("AA") else "BB"
            )
            with pytest.raises(MCPError) as excinfo:
                await client.session.call_tool(
                    "guard",
                    {},
                    input_responses={"x": {"action": "accept", "content": {"x": "v"}}},
                    request_state=tampered,
                    allow_input_required=True,
                )
        assert "requestState" in str(excinfo.value)

    async def test_plaintext_round_trips_to_tool(self):
        """A valid echo delivers the original plaintext back to the tool."""
        async with Client(self._one_shot_server(), mode="auto") as client:
            first = await client.session.call_tool(
                "guard", {}, allow_input_required=True
            )
            assert isinstance(first, InputRequiredResult)
            second = await client.session.call_tool(
                "guard",
                {},
                input_responses={"x": {"action": "accept", "content": {"x": "v"}}},
                request_state=first.request_state,
                allow_input_required=True,
            )
        assert isinstance(second, mcp_types.CallToolResult)
        assert second.structured_content == {"result": "state=PLAINTEXT-STATE"}


class TestMiddlewareInteraction:
    async def test_suspend_survives_error_handling_middleware(self):
        """The suspension signal must not be swallowed by error middleware.

        ErrorHandlingMiddleware (and any third-party middleware) legitimately
        uses a broad ``except Exception``; the suspend travels as a
        ``BaseException`` subclass precisely so it passes through untouched.
        """

        class BroadCatchMiddleware(Middleware):
            async def on_call_tool(self, context, call_next):
                try:
                    return await call_next(context)
                except Exception as e:  # the trap the signal must escape
                    raise RuntimeError(f"swallowed: {e}") from e

        server = two_question_server()
        server.add_middleware(ErrorHandlingMiddleware())
        server.add_middleware(BroadCatchMiddleware())

        asked: list[str] = []
        async with Client(
            server, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("book_flight", {})
        assert result.data == "Booked Paris on 2026-08-01"
        assert len(asked) == 2


class TestAnnotatedReturns:
    async def test_annotated_union_return_strips_guard_arm(self):
        """``Annotated[str | InputRequiredResult, ...]`` derives its output
        schema from the data arm; the guard arm is stripped inside Annotated."""
        mcp = FastMCP("AnnotatedGuard")

        @mcp.tool
        async def greet(
            ctx: Context,
        ) -> Annotated[str | InputRequiredResult, Field(description="greeting")]:
            if ctx.input_responses is None:
                return InputRequiredResult(
                    input_requests={"name": _elicit("name", "Your name?", "name")},
                    request_state="",
                )
            return "hello"

        tool = await mcp.get_tool("greet")
        assert tool is not None
        schema = tool.output_schema
        assert schema is not None
        # Derived from the str arm — not poisoned by the guard arm.
        assert "InputRequired" not in str(schema)

    async def test_metadata_on_guard_arm_is_stripped(self):
        """When only the guard arm carries metadata
        (``str | Annotated[InputRequiredResult, Field(...)]``), it is still
        recognized and stripped so the str arm's schema survives."""
        mcp = FastMCP("AnnotatedGuardArm")

        @mcp.tool
        async def greet(
            ctx: Context,
        ) -> str | Annotated[InputRequiredResult, Field(description="suspend")]:
            if ctx.input_responses is None:
                return InputRequiredResult(
                    input_requests={"name": _elicit("name", "Your name?", "name")},
                    request_state="",
                )
            return "hello"

        tool = await mcp.get_tool("greet")
        assert tool is not None
        schema = tool.output_schema
        assert schema is not None
        assert "InputRequired" not in str(schema)


class TestRequestStateSecurityConfig:
    def test_custom_security_requires_stable_audience(self):
        """A shared-key policy with neither an explicit audience nor a stable
        server name would stamp per-replica random audiences — rejected at
        construction with a clear remedy."""
        with pytest.raises(ValueError, match="stable audience"):
            FastMCP(request_state_security=RequestStateSecurity(keys=[b"0" * 32]))

        # Either remedy works:
        FastMCP(
            name="Stable",
            request_state_security=RequestStateSecurity(keys=[b"0" * 32]),
        )
        FastMCP(
            request_state_security=RequestStateSecurity(
                keys=[b"0" * 32], audience="my-service"
            )
        )

    async def test_explicit_shared_keys_seal_and_complete(self):
        """A server configured with explicit shared keys drives a full loop —
        the multi-replica configuration (RequestStateSecurity(keys=[...]))."""
        key = b"0" * 32
        server = two_question_server(
            request_state_security=RequestStateSecurity(keys=[key])
        )
        asked: list[str] = []
        async with Client(
            server, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("book_flight", {})
        assert result.data == "Booked Paris on 2026-08-01"

    def _sealing_server(self, key: bytes) -> FastMCP:
        """A one-round guard that seals a request_state on its first ask, under
        an explicit key so state can be replayed across instances."""
        mcp = FastMCP(
            "seal-keyed",
            request_state_security=RequestStateSecurity(keys=[key]),
        )

        @mcp.tool
        async def guard(ctx: Context) -> str | InputRequiredResult:
            if ctx.input_responses is None:
                return _ask(_elicit("x", "q", "x"), "x", request_state="carried")
            return f"state={ctx.request_state}"

        return mcp

    async def test_state_from_a_different_key_is_rejected(self):
        """State minted under one key is rejected by a server holding a
        different key — the cross-instance isolation shared keys prevent."""
        async with Client(self._sealing_server(b"a" * 32), mode="auto") as client:
            first = await client.session.call_tool(
                "guard", {}, allow_input_required=True
            )
            assert isinstance(first, InputRequiredResult)
            state_from_a = first.request_state
        assert state_from_a is not None

        async with Client(self._sealing_server(b"b" * 32), mode="auto") as client:
            with pytest.raises(MCPError):
                await client.session.call_tool(
                    "guard",
                    {},
                    input_responses={"x": {"action": "accept", "content": {"x": "v"}}},
                    request_state=state_from_a,
                    allow_input_required=True,
                )


class TestHttpTransport:
    async def test_two_question_loop_over_http(self):
        """The full guard loop completes over Streamable HTTP with mode='auto'."""
        asked: list[str] = []
        async with run_server_async(two_question_server()) as url:
            async with Client(
                StreamableHttpTransport(url),
                mode="auto",
                elicitation_handler=_two_answer_handler(asked),
            ) as client:
                assert client.protocol_version == "2026-07-28"
                result = await client.call_tool("book_flight", {})

        assert asked == ["Where would you like to fly?", "When to Paris?"]
        assert result.data == "Booked Paris on 2026-08-01"
