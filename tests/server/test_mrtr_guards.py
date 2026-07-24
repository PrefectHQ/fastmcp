"""Server-side guard-mode multi-round-trip (MRTR, SEP-2322).

A FastMCP tool may return an ``InputRequiredResult`` as the full result of a
call: the client fulfils the embedded requests (elicitation / sampling /
roots) and calls again — a new, complete request-response cycle — with the
answers on ``ctx.input_responses`` and the echoed opaque ``ctx.request_state``.
This is the SDK's own base "guard" model — the tool runs per round and checks
whether the client's answers are present — with FastMCP mirroring its
semantics exactly.

These tests exercise the *emission* side (a FastMCP server producing the
``InputRequiredResult`` and being driven to completion) over both in-memory and
HTTP transports, plus the request-state boundary (framework-owned sealing) and
the ≤2025-11-25 era gate. The client-side *answering* path is covered by
``tests/client/client/test_input_required_driver.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import mcp_types
import pytest
from docket import Docket
from mcp.client._input_required import InputRequiredRoundsExceededError
from mcp.server.request_state import RequestStateSecurity
from mcp.shared.exceptions import MCPError
from mcp_types import ElicitRequest, InputRequiredResult
from mcp_types.version import MODERN_PROTOCOL_VERSIONS
from pydantic import Field
from typing_extensions import TypeAliasType

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.middleware import Middleware
from fastmcp.tools.base import InputRequiredToolResult, ToolResult
from fastmcp.utilities.tests import run_server_async
from fastmcp_tasks import TasksExtension
from tests.tasks.task_helpers import running_task_server, submit_task, wait_for_task


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


# PEP 695 `type X = ...` aliases, built portably (the `type` statement is 3.12+).
# One factors out the whole guard union; one is a lone aliased ask arm; one is a
# composed alias whose union arm hides the guard (`str | _ComposedGuardArm`).
_AliasedGuardUnion = TypeAliasType("_AliasedGuardUnion", str | InputRequiredResult)
_AliasedAskArm = TypeAliasType("_AliasedAskArm", InputRequiredResult)
_ComposedGuardArm = TypeAliasType("_ComposedGuardArm", int | InputRequiredResult)


class _InputRequiredSubclass(InputRequiredResult):
    """A user subclass of the guard result — still a control signal, not data."""


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

    def test_aliased_guard_union_stripped(self):
        """A `type Result = str | InputRequiredResult` alias is unwrapped before
        stripping, so the ask arm never leaks into the output schema."""
        from fastmcp.tools.function_tool import FunctionTool

        def book(x: int) -> _AliasedGuardUnion:
            return "ok"

        tool = FunctionTool.from_function(book)
        assert tool.output_schema is not None
        assert tool.output_schema.get("x-fastmcp-wrap-result") is True

    def test_aliased_ask_arm_stripped(self):
        """A lone aliased arm (`str | AskAlias`) is recognized as a guard signal
        and stripped, leaving the data arm's schema."""
        from fastmcp.tools.function_tool import FunctionTool

        def book(x: int) -> str | _AliasedAskArm:
            return "ok"

        tool = FunctionTool.from_function(book)
        assert tool.output_schema is not None
        assert tool.output_schema.get("x-fastmcp-wrap-result") is True

    def test_composed_alias_arm_stripped(self):
        """A composed alias arm — `str | Value` where
        `Value = int | InputRequiredResult` — is recursively unwrapped so the
        hidden guard is stripped, leaving the flattened data arms (`str | int`)."""
        from typing import get_args as _get_args

        from fastmcp.tools.function_parsing import _strip_input_required

        stripped = _strip_input_required(str | _ComposedGuardArm)
        assert set(_get_args(stripped)) == {str, int}

    def test_bare_input_required_subclass_suppresses_schema(self):
        """A bare `InputRequiredResult` *subclass* is subclass-aware suppressed,
        matching `run()`'s isinstance handling, so no output schema is emitted
        for data the client can never receive."""
        from fastmcp.tools.function_tool import FunctionTool

        def suspend_only(x: int) -> _InputRequiredSubclass:
            raise NotImplementedError

        tool = FunctionTool.from_function(suspend_only)
        assert tool.output_schema is None

    def test_bare_aliased_input_required_suppresses_schema(self):
        """A bare aliased guard return (`-> _AliasedAskArm` where the alias is
        `InputRequiredResult`) is de-aliased so downstream suppression applies,
        emitting no output schema."""
        from fastmcp.tools.function_tool import FunctionTool

        def suspend_only(x: int) -> _AliasedAskArm:
            raise NotImplementedError

        tool = FunctionTool.from_function(suspend_only)
        assert tool.output_schema is None

    def test_annotated_bare_guard_returns_suppress_schema(self):
        """The wholesale suppression also covers Annotated wrappings of a bare
        guard return — including an aliased one — which exact-match replacement
        would otherwise miss."""
        from fastmcp.tools.function_tool import FunctionTool

        def annotated_plain(x: int) -> Annotated[InputRequiredResult, Field()]:
            raise NotImplementedError

        def annotated_aliased(x: int) -> Annotated[_AliasedAskArm, Field()]:
            raise NotImplementedError

        assert FunctionTool.from_function(annotated_plain).output_schema is None
        assert FunctionTool.from_function(annotated_aliased).output_schema is None

    def test_bare_input_required_return_suppresses_schema(self):
        from fastmcp.tools.function_tool import FunctionTool

        def suspend_only(x: int) -> InputRequiredResult:
            raise NotImplementedError

        tool = FunctionTool.from_function(suspend_only)
        assert tool.output_schema is None


class TestTransformedGuard:
    """A guard tool wrapped by TransformedTool must still emit its ask: a
    non-object output_schema on the transform reshapes ordinary ToolResults,
    but an InputRequiredToolResult (which carries no output data) must pass
    through so the wire handler still returns the InputRequiredResult."""

    async def test_transformed_guard_still_asks(self):
        from fastmcp.tools.tool_transform import TransformedTool

        base = two_question_server()
        book = await base.get_tool("book_flight")
        assert book is not None

        mcp = FastMCP("transformed")
        # A non-object output_schema is exactly the transform config that
        # rebuilds ordinary ToolResults and would strip the ask.
        transformed = TransformedTool.from_tool(
            book, name="book", output_schema={"type": "string"}
        )
        mcp.add_tool(transformed)

        # The asking (first) round must reach the wire as an InputRequiredResult
        # — without the guard it would arrive as an empty terminal result.
        async with Client(mcp, mode="auto") as client:
            first = await client.session.call_tool(
                "book", {}, allow_input_required=True
            )
        assert isinstance(first, InputRequiredResult)
        assert "destination" in first.input_requests

    async def test_transform_fn_returning_raw_ask_is_wrapped(self):
        """A custom transform_fn that returns a raw InputRequiredResult (not a
        pre-wrapped InputRequiredToolResult) must still emit the ask — the
        transform path wraps it like any tool body."""
        from fastmcp.tools.tool_transform import TransformedTool

        base = two_question_server()
        book = await base.get_tool("book_flight")
        assert book is not None

        async def transform_fn(**kwargs) -> InputRequiredResult:
            return _ask(_elicit("q", "raw ask?", "q"), "q", request_state=None)

        mcp = FastMCP("raw-transform")
        transformed = TransformedTool.from_tool(
            book, name="raw", transform_fn=transform_fn
        )
        mcp.add_tool(transformed)

        async with Client(mcp, mode="auto") as client:
            first = await client.session.call_tool("raw", {}, allow_input_required=True)
        assert isinstance(first, InputRequiredResult)
        assert "q" in first.input_requests


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
        parent's namespaced name — the InputRequiredToolResult forwards through
        the provider delegation and is sealed/returned at the parent's wire seam."""
        parent = FastMCP("parent")
        parent.mount(two_question_server(), namespace="sub")

        asked: list[str] = []
        async with Client(
            parent, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("sub_book_flight", {})

        assert asked == ["Where would you like to fly?", "When to Paris?"]
        assert result.data == "Booked Paris on 2026-08-01"


def _modern_proxy(backend: FastMCP) -> FastMCP:
    """A FastMCPProxy whose backend client negotiates the modern era, so the
    backend can emit an `InputRequiredResult` (SEP-2322) for the proxy to
    round-trip rather than drive to completion internally."""
    from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

    return FastMCPProxy(client_factory=lambda: ProxyClient(backend, mode="auto"))


class TestProxyServer:
    async def test_guard_tool_round_trips_through_proxy(self):
        """A guard tool behind a proxy completes end-to-end: the backend's ask
        round-trips into an `InputRequiredToolResult` on the parent (rather than
        being driven to completion inside the proxy), so the parent's wire
        handler returns it to the real client, and continuation answers forward
        back down to the backend."""
        proxy = _modern_proxy(two_question_server())

        asked: list[str] = []
        async with Client(
            proxy, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("book_flight", {})

        assert asked == ["Where would you like to fly?", "When to Paris?"]
        assert result.data == "Booked Paris on 2026-08-01"

    async def test_proxy_parent_middleware_observes_ask(self):
        """The proxy round-trip is what lets the parent's own middleware see the
        ask as a result — it is not swallowed by driving inside the proxy."""
        observed: list[object] = []

        class RecordingMiddleware(Middleware):
            async def on_call_tool(self, context, call_next):
                result = await call_next(context)
                observed.append(result)
                return result

        proxy = _modern_proxy(two_question_server())
        proxy.add_middleware(RecordingMiddleware())

        asked: list[str] = []
        async with Client(
            proxy, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("book_flight", {})

        assert result.data == "Booked Paris on 2026-08-01"
        assert len(observed) == 3
        assert isinstance(observed[0], InputRequiredToolResult)
        assert isinstance(observed[1], InputRequiredToolResult)
        assert not isinstance(observed[2], InputRequiredToolResult)

    async def test_guard_round_trips_through_create_proxy_mode_auto(self):
        """The standard create_proxy(target, mode="auto") path round-trips a
        guard without a hand-built ProxyClient factory. The default stays
        handshake-era (which preserves server-initiated push forwarding); modern
        proxying is a per-call opt-in because the two eras are mutually
        exclusive on a single proxy session."""
        from fastmcp.server import create_proxy

        proxy = create_proxy(two_question_server(), mode="auto")

        asked: list[str] = []
        async with Client(
            proxy, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("book_flight", {})

        assert asked == ["Where would you like to fly?", "When to Paris?"]
        assert result.data == "Booked Paris on 2026-08-01"

    async def test_progress_forwards_through_modern_proxy(self):
        """A backend tool's `ctx.report_progress()` reaches the caller's progress
        handler even when the proxy backend negotiates the modern era — the
        modern branch forwards the proxy client's progress handler just like the
        legacy `call_tool_mcp` path does."""
        backend = FastMCP("progress-backend")

        @backend.tool
        async def report(ctx: Context) -> str:
            await ctx.report_progress(progress=1, total=2, message="halfway")
            await ctx.report_progress(progress=2, total=2, message="done")
            return "ok"

        proxy = _modern_proxy(backend)

        received: list[tuple[float, float | None, str | None]] = []

        async def progress_handler(progress, total, message):
            received.append((progress, total, message))

        async with Client(
            proxy, mode="auto", progress_handler=progress_handler
        ) as client:
            result = await client.call_tool("report", {})

        assert result.data == "ok"
        assert received == [(1, 2, "halfway"), (2, 2, "done")]


@dataclass
class _Person:
    name: str


def _era_reporting_backend() -> FastMCP:
    """A dual-era backend for the mirroring tests.

    Hosts the three-round guard tool (``book_flight``), a tool that reports the
    protocol version its own backend session negotiated (``backend_era``), and a
    server-initiated elicitation tool (``ask_name``) that only works when the
    session is handshake-era, so a single backend proves which era the proxy
    mirrored onto it.
    """
    mcp = two_question_server()

    @mcp.tool
    async def backend_era(ctx: Context) -> str:
        rc = ctx.request_context
        assert rc is not None
        return rc.protocol_version

    @mcp.tool
    async def ask_name(ctx: Context) -> str:
        result = await ctx.elicit("What is your name?", response_type=_Person)
        if result.action == "accept":
            assert isinstance(result.data, _Person)
            return f"Hello, {result.data.name}!"
        return "no name"

    return mcp


class TestProxyEraMirroring:
    """A proxy created from a non-Client target with no explicit mode mirrors the
    front connection's negotiated era onto its backend session, so the whole
    chain speaks one era end-to-end."""

    async def test_modern_front_mirrors_modern_backend(self):
        """A modern front through a proxy with NO explicit mode gets a modern
        backend session, so a guard tool round-trips end-to-end."""
        from fastmcp.server import create_proxy

        proxy = create_proxy(_era_reporting_backend())

        asked: list[str] = []
        async with Client(
            proxy, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            era = await client.call_tool("backend_era", {})
            result = await client.call_tool("book_flight", {})

        assert era.data == "2026-07-28"
        assert result.data == "Booked Paris on 2026-08-01"

    async def test_legacy_front_mirrors_handshake_backend(self):
        """A legacy front through a proxy with NO explicit mode gets a handshake
        backend session, so server-initiated elicitation push-forwards through
        the proxy to the front client's handler."""
        from fastmcp.server import create_proxy

        proxy = create_proxy(_era_reporting_backend())

        async def name_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content=response_type(name="Ada"))

        async with Client(
            proxy, mode="legacy", elicitation_handler=name_handler
        ) as client:
            era = await client.call_tool("backend_era", {})
            greeting = await client.call_tool("ask_name", {})

        assert era.data not in MODERN_PROTOCOL_VERSIONS
        assert greeting.data == "Hello, Ada!"

    async def test_same_proxy_serves_both_eras_without_bleed(self):
        """The SAME proxy instance serves a legacy front and a modern front (and
        a legacy front again); each gets its matching backend era. This is the
        session-cache trap: a backend session pinned to one era must never be
        reused across front connections of a different era."""
        from fastmcp.server import create_proxy

        proxy = create_proxy(_era_reporting_backend())

        async with Client(proxy, mode="legacy") as client:
            legacy_era = await client.call_tool("backend_era", {})
        async with Client(proxy, mode="auto") as client:
            modern_era = await client.call_tool("backend_era", {})
        async with Client(proxy, mode="legacy") as client:
            legacy_again = await client.call_tool("backend_era", {})

        assert legacy_era.data not in MODERN_PROTOCOL_VERSIONS
        assert modern_era.data == "2026-07-28"
        assert legacy_again.data not in MODERN_PROTOCOL_VERSIONS

    async def test_explicit_mode_overrides_mirroring(self):
        """An explicit ``create_proxy(mode=...)`` pins the backend era regardless
        of the front connection's era, overriding mirroring."""
        from fastmcp.server import create_proxy

        proxy = create_proxy(_era_reporting_backend(), mode="auto")

        # Legacy front, but the backend is pinned modern by the explicit mode.
        async with Client(proxy, mode="legacy") as client:
            era = await client.call_tool("backend_era", {})

        assert era.data == "2026-07-28"


class TestMultiServerConfigEraMirroring:
    """A multi-server `MCPConfig` target puts an extra hop between the proxy and
    the real backends: `MCPConfigTransport` mounts one proxy per configured
    server on a composite router. Setting the era on the outer client alone
    would stop at that router, leaving every real backend on its own default
    era, so the mirrored era has to reach the mounted legs too.
    """

    @staticmethod
    def _config(url: str) -> dict[str, object]:
        """Two entries so the transport takes its multi-server composite path."""
        return {"mcpServers": {"a": {"url": url}, "b": {"url": url}}}

    async def test_modern_front_reaches_modern_backends(self):
        """A modern front reaches each real backend on a modern session, and a
        backend guard tool round-trips end to end across both proxy hops."""
        from fastmcp.server import create_proxy

        async with run_server_async(_era_reporting_backend()) as url:
            proxy = create_proxy(self._config(url))

            asked: list[str] = []
            async with Client(
                proxy, mode="auto", elicitation_handler=_two_answer_handler(asked)
            ) as client:
                era = await client.call_tool("a_backend_era", {})
                result = await client.call_tool("a_book_flight", {})

        assert era.data == "2026-07-28"
        assert result.data == "Booked Paris on 2026-08-01"
        assert len(asked) == 2

    async def test_legacy_front_reaches_handshake_backends(self):
        """A legacy front reaches each real backend on a handshake session, so
        server-initiated elicitation still push-forwards up the whole chain."""
        from fastmcp.server import create_proxy

        async def name_handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content=response_type(name="Ada"))

        async with run_server_async(_era_reporting_backend()) as url:
            proxy = create_proxy(self._config(url))

            async with Client(
                proxy, mode="legacy", elicitation_handler=name_handler
            ) as client:
                era = await client.call_tool("a_backend_era", {})
                greeting = await client.call_tool("a_ask_name", {})

        assert era.data not in MODERN_PROTOCOL_VERSIONS
        assert greeting.data == "Hello, Ada!"

    async def test_explicit_mode_overrides_mirroring(self):
        """An explicit ``create_proxy(mode=...)`` pins the era all the way down,
        overriding what the front negotiated."""
        from fastmcp.server import create_proxy

        async with run_server_async(_era_reporting_backend()) as url:
            proxy = create_proxy(self._config(url), mode="auto")

            async with Client(proxy, mode="legacy") as client:
                era = await client.call_tool("a_backend_era", {})

        assert era.data == "2026-07-28"


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
    """Each MRTR leg is a complete request→response cycle: an
    `InputRequiredResult` is the full, legitimate result of that leg, so it
    flows back through the middleware chain as an ordinary `ToolResult`
    (specifically an `InputRequiredToolResult`). Middleware observes it as a
    normal result, not an error and not control flow."""

    async def test_completes_with_error_and_broad_except_middleware(self):
        """An ask is a result, not an error, so error-handling middleware and a
        broad ``except Exception`` are simply irrelevant — `call_next` returns
        the ask, nothing is raised, and the loop completes normally.
        """

        class BroadCatchMiddleware(Middleware):
            async def on_call_tool(self, context, call_next):
                # The ask is RETURNED here, never raised, so this except never
                # fires on a guard round.
                try:
                    return await call_next(context)
                except Exception as e:
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

    async def test_middleware_observes_ask_then_final_result_per_leg(self):
        """A logging-style recording middleware sees the full chain run on every
        leg: the ask (`InputRequiredToolResult`) is the observed result on the
        two guard legs, and the terminal string is the result on the last leg.
        Three client-visible rounds ⇒ three `on_call_tool` fires."""
        observed: list[object] = []

        class RecordingMiddleware(Middleware):
            async def on_call_tool(self, context, call_next):
                result = await call_next(context)
                observed.append(result)
                return result

        server = two_question_server()
        server.add_middleware(RecordingMiddleware())

        asked: list[str] = []
        async with Client(
            server, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("book_flight", {})

        assert result.data == "Booked Paris on 2026-08-01"
        # One on_call_tool fire per leg — three legs, three observations.
        assert len(observed) == 3
        # The first two legs each return the ask as their result; middleware can
        # identify it by isinstance on the ToolResult subclass.
        assert isinstance(observed[0], InputRequiredToolResult)
        assert isinstance(observed[1], InputRequiredToolResult)
        # Each ask carries the underlying InputRequiredResult unmodified.
        assert isinstance(observed[0].input_required, InputRequiredResult)
        # The final leg returns the ordinary terminal result, not an ask.
        assert isinstance(observed[2], ToolResult)
        assert not isinstance(observed[2], InputRequiredToolResult)
        assert observed[2].structured_content == {
            "result": "Booked Paris on 2026-08-01"
        }

    async def test_continuation_leg_is_detectable_from_context(self):
        """Middleware can distinguish an initial leg from a continuation leg via
        ``context.fastmcp_context.input_responses`` — ``None`` on the first
        round, present once the client has answered."""
        input_responses_seen: list[bool] = []

        class DetectMiddleware(Middleware):
            async def on_call_tool(self, context, call_next):
                fctx = context.fastmcp_context
                assert fctx is not None
                input_responses_seen.append(fctx.input_responses is not None)
                return await call_next(context)

        server = two_question_server()
        server.add_middleware(DetectMiddleware())

        asked: list[str] = []
        async with Client(
            server, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("book_flight", {})

        assert result.data == "Booked Paris on 2026-08-01"
        # Leg 1 has no answers yet; legs 2 and 3 are continuations.
        assert input_responses_seen == [False, True, True]

    async def test_continuation_fields_populate_message(self):
        """The continuation fields (SEP-2322) appear on ``context.message``
        itself, not only on ``fastmcp_context`` — so middleware branching on the
        standard `input_responses` / `request_state` params sees a continuation
        round as such rather than as an initial call."""
        responses_seen: list[bool] = []
        state_seen: list[bool] = []

        class MessageMiddleware(Middleware):
            async def on_call_tool(self, context, call_next):
                responses_seen.append(context.message.input_responses is not None)
                state_seen.append(context.message.request_state is not None)
                return await call_next(context)

        server = two_question_server()
        server.add_middleware(MessageMiddleware())

        asked: list[str] = []
        async with Client(
            server, mode="auto", elicitation_handler=_two_answer_handler(asked)
        ) as client:
            result = await client.call_tool("book_flight", {})

        assert result.data == "Booked Paris on 2026-08-01"
        # Answers arrive on legs 2 and 3; request_state is carried only on leg 3
        # (round 2's ask minted it), so it is absent on legs 1 and 2.
        assert responses_seen == [False, True, True]
        assert state_seen == [False, False, True]


class TestCachingMiddlewareInteraction:
    async def test_ask_is_not_cached(self):
        """The response cache must never store an ask: two identical first-leg
        calls both reach the tool (the second is not served a stale cached
        question). A cached ask would replay a stale prompt and skip the tool's
        own per-round logic."""
        from fastmcp.server.middleware.caching import ResponseCachingMiddleware

        call_count = {"n": 0}
        mcp = FastMCP("cache-guard")

        @mcp.tool
        async def ask(ctx: Context) -> str | InputRequiredResult:
            if ctx.input_responses is None:
                call_count["n"] += 1
                return _ask(_elicit("x", "q", "x"), "x", None)
            return "done"

        mcp.add_middleware(ResponseCachingMiddleware())

        # Two independent first legs (no elicitation handler ⇒ each raises on the
        # client driver rather than completing), but both must have reached the
        # tool body — proving the ask was not served from cache.
        async with Client(mcp, mode="auto") as client:
            for _ in range(2):
                first = await client.session.call_tool(
                    "ask", {}, allow_input_required=True
                )
                assert isinstance(first, InputRequiredResult)

        assert call_count["n"] == 2

    async def test_state_only_continuation_final_not_cached(self):
        """A state-only round (request_state, no questions) retries with
        input_responses=None — request_state alone must mark the continuation,
        or its terminal result would be cached under the fresh-call key."""
        from fastmcp.server.middleware.caching import ResponseCachingMiddleware

        body_runs = {"n": 0}
        mcp = FastMCP("cache-state-only")

        @mcp.tool
        async def staged(ctx: Context) -> str | InputRequiredResult:
            body_runs["n"] += 1
            if ctx.request_state is None:
                return InputRequiredResult(
                    result_type="input_required",
                    input_requests={},
                    request_state="stage=1",
                )
            return f"done after {ctx.request_state}"

        mcp.add_middleware(ResponseCachingMiddleware())

        async with Client(mcp, mode="auto") as client:
            first = await client.session.call_tool(
                "staged", {}, allow_input_required=True
            )
            assert isinstance(first, InputRequiredResult)
            # The client echoes the (sealed) state with no responses — the
            # state-only continuation the guard must recognize.
            final = await client.session.call_tool(
                "staged",
                {},
                request_state=first.request_state,
                allow_input_required=True,
            )
            assert not isinstance(final, InputRequiredResult)
            runs_after_flow = body_runs["n"]

            # A fresh identical call must run the tool again, not be served
            # the continuation's cached final.
            fresh = await client.session.call_tool(
                "staged", {}, allow_input_required=True
            )
            assert isinstance(fresh, InputRequiredResult)

        assert body_runs["n"] == runs_after_flow + 1

    async def test_completed_flow_final_result_not_served_to_fresh_call(self):
        """A continuation leg's final result must not enter the cache: its key
        is built from name+arguments only, identical to a fresh call's — so a
        cached final would be served to the next fresh call, which would then
        never be asked the tool's questions."""
        from fastmcp.server.middleware.caching import ResponseCachingMiddleware

        body_runs = {"n": 0}
        mcp = FastMCP("cache-flow")

        @mcp.tool
        async def ask(ctx: Context) -> str | InputRequiredResult:
            body_runs["n"] += 1
            if ctx.input_responses is None:
                return _ask(_elicit("x", "q", "x"), "x", None)
            return "done"

        mcp.add_middleware(ResponseCachingMiddleware())

        async def answer(message, response_type, params, ctx):
            return ElicitResult(action="accept", content=response_type(x="a"))

        async with Client(mcp, mode="auto", elicitation_handler=answer) as client:
            first = await client.call_tool("ask", {})
            assert first.data == "done"
            runs_after_first_flow = body_runs["n"]

            # A fresh identical call must run the tool again (ask + answer),
            # not be served the previous flow's cached final answer.
            second = await client.call_tool("ask", {})
            assert second.data == "done"

        assert body_runs["n"] == runs_after_first_flow + 2


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
    def test_custom_security_without_stable_audience_warns(self, caplog):
        """A supplied policy with neither an explicit audience nor a stable
        server name warns (shared keys across replicas would stamp per-replica
        random audiences) — but constructs, since single-process customization
        (e.g. an ephemeral policy with a custom ttl) is legitimate and a policy
        object cannot reveal whether its keys are shared."""
        import logging

        with caplog.at_level(logging.WARNING):
            FastMCP(request_state_security=RequestStateSecurity(keys=[b"0" * 32]))
        assert any("stable audience" in r.message for r in caplog.records)

        # An empty name is falsy → still a random per-replica name, so it must
        # warn like an omitted name (not slip through a `name is None` check).
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            FastMCP(
                name="", request_state_security=RequestStateSecurity(keys=[b"0" * 32])
            )
        assert any("stable audience" in r.message for r in caplog.records)

        # Single-process customization is allowed (warns, does not raise):
        FastMCP(request_state_security=RequestStateSecurity.ephemeral(ttl=30))

        # Either remedy avoids the warning:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            FastMCP(
                name="Stable",
                request_state_security=RequestStateSecurity(keys=[b"0" * 32]),
            )
            FastMCP(
                request_state_security=RequestStateSecurity(
                    keys=[b"0" * 32], audience="my-service"
                )
            )
        assert not any("stable audience" in r.message for r in caplog.records)

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


class TestTaskExecution:
    """A guard tool returns an `InputRequiredResult` as its result, which only
    makes sense against a live request that can answer the prompt. A detached
    background task has no such request, so returning a guard result from a task
    is rejected with a clear error rather than silently yielding empty content."""

    @pytest.fixture
    def reset_docket_memory_server(self):
        """Force a fresh memory:// Docket server bound to this test's loop."""
        if hasattr(Docket, "_memory_server"):
            delattr(Docket, "_memory_server")
        yield
        if hasattr(Docket, "_memory_server"):
            delattr(Docket, "_memory_server")

    async def test_guard_result_from_task_parks_for_input(
        self, reset_docket_memory_server
    ):
        mcp = FastMCP("guard-task")
        mcp.add_extension(TasksExtension())

        @mcp.tool(task=True)
        async def book_flight(ctx: Context) -> str | InputRequiredResult:
            return _ask(
                _elicit("date", "When?", "date"),
                key="date",
                request_state=None,
            )

        # A function-tool guard is driven as a task by the in-task reentrant
        # loop: submitting `book_flight` parks its input request on the poll
        # surface (`input_required`), where a client answers it via
        # `tasks/update`. The full round-trip lives in
        # tests/tasks/server/test_guard_reentrant.py.
        async with running_task_server(mcp):
            created = await submit_task(mcp, "book_flight", {})
            parked = await wait_for_task(
                mcp,
                created.task_id,
                target_states=frozenset({"input_required"}),
            )
            assert parked.status == "input_required"
            assert parked.input_requests


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
