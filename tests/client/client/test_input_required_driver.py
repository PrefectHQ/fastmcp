"""Client-side SEP-2322 multi-round-trip (MRTR) input-required driving.

A 2026-07-28 server may answer `tools/call` / `prompts/get` / `resources/read`
with an `InputRequiredResult` carrying embedded sampling / elicitation / roots
requests instead of the terminal result. `fastmcp.Client` fulfils those embedded
requests through its *existing* handler callbacks (the same table that answers
legacy server-initiated RPCs) and retries until the call resolves.

The 2026 emitter here is the SDK's own high-level `MCPServer` with declarative
`Resolve` / `Elicit` resolvers: at 2026-07-28 the framework batches a resolver's
`Elicit` into an `InputRequiredResult`. It is driven in-memory through a
`fastmcp.Client` with `mode="auto"` (which negotiates 2026-07-28 over the
dual-era stream loop), so the whole answer path runs through FastMCP's client.

Server-side MRTR *emission* is a maintainer-held design surface; these tests only
exercise the client-side *answering* path, which the spec's decisive finding says
is era-neutral by construction once the driver is wired.
"""

from typing import Annotated, cast

import pytest
from mcp.client._input_required import (
    DEFAULT_INPUT_REQUIRED_MAX_ROUNDS,
    InputRequiredRoundsExceededError,
)
from mcp.server.mcpserver import Elicit, MCPServer, Resolve
from pydantic import BaseModel

from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitResult


class _Name(BaseModel):
    name: str


def _ask_name() -> Elicit[_Name]:
    return Elicit("What is your name?", _Name)


class _A(BaseModel):
    a: str


class _B(BaseModel):
    b: str


def _ask_a() -> Elicit[_A]:
    return Elicit("question A", _A)


def _ask_b(dep: Annotated[_A, Resolve(_ask_a)]) -> Elicit[_B]:
    """A resolver depending on another's answer — asked in a later round."""
    return Elicit(f"question B given {dep.a}", _B)


def _multi_round_server() -> MCPServer:
    server = MCPServer("multi")

    @server.tool()
    def combine(x: Annotated[_B, Resolve(_ask_b)]) -> str:
        return f"got {x.b}"

    return server


def _mrtr_server() -> MCPServer:
    """An SDK MCPServer whose `greet` tool elicits a name via a resolver.

    At 2026-07-28 the elicitation is delivered as an `InputRequiredResult`.
    """
    server = MCPServer("mrtr")

    @server.tool()
    def greet(who: Annotated[_Name, Resolve(_ask_name)]) -> str:
        return f"Hello, {who.name}!"

    return server


class TestParamDefaults:
    def test_default_max_rounds(self):
        client = Client(FastMCP("x"))
        assert client.input_required_max_rounds == DEFAULT_INPUT_REQUIRED_MAX_ROUNDS

    def test_custom_max_rounds(self):
        client = Client(FastMCP("x"), input_required_max_rounds=3)
        assert client.input_required_max_rounds == 3

    def test_new_preserves_max_rounds(self):
        parent = Client(FastMCP("x"), input_required_max_rounds=4)
        assert parent.new().input_required_max_rounds == 4


class TestCallToolMRTR:
    async def test_single_round_completes(self):
        """One full MRTR round: the server asks, the client's elicitation_handler
        answers, and the tool completes with the answered value."""
        asked: list[str] = []

        async def handler(message, response_type, params, ctx):
            asked.append(message)
            return ElicitResult(action="accept", content=response_type(name="Ada"))

        async with Client(
            _mrtr_server(), mode="auto", elicitation_handler=handler
        ) as client:
            assert client.protocol_version == "2026-07-28"
            result = await client.call_tool("greet", {})

        # The embedded elicitation was answered exactly once and the terminal
        # result carries the answered value. The SDK MCPServer wraps scalar
        # output as {"result": ...}, surfaced via structured_content.
        assert asked == ["What is your name?"]
        assert result.structured_content == {"result": "Hello, Ada!"}
        assert result.data.result == "Hello, Ada!"

    async def test_data_parsed_on_terminal_result_only(self):
        """`.data` deserialization runs on the resolved terminal result, never on
        the intermediate InputRequiredResult."""

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content=response_type(name="Bob"))

        async with Client(
            _mrtr_server(), mode="auto", elicitation_handler=handler
        ) as client:
            result = await client.call_tool("greet", {})

        assert result.is_error is False
        assert result.data.result == "Hello, Bob!"
        assert result.structured_content == {"result": "Hello, Bob!"}

    async def test_multi_round_dependent_resolvers(self):
        """A resolver depending on another's answer is asked in a later round; the
        driver loops until the dependent chain terminates."""
        rounds: list[str] = []

        async def handler(message, response_type, params, ctx):
            rounds.append(message)
            if "question A" in message:
                return ElicitResult(action="accept", content=response_type(a="AA"))
            return ElicitResult(action="accept", content=response_type(b="BB"))

        async with Client(
            _multi_round_server(), mode="auto", elicitation_handler=handler
        ) as client:
            result = await client.call_tool("combine", {})

        # Two rounds: A first, then B (which the client saw carried A's answer).
        assert rounds == ["question A", "question B given AA"]
        assert result.structured_content == {"result": "got BB"}

    async def test_max_rounds_exceeded_raises(self):
        """A dependent chain needing two rounds under `max_rounds=1` raises."""

        async def handler(message, response_type, params, ctx):
            field = "a" if "question A" in message else "b"
            return ElicitResult(action="accept", content=response_type(**{field: "v"}))

        async with Client(
            _multi_round_server(),
            mode="auto",
            elicitation_handler=handler,
            input_required_max_rounds=1,
        ) as client:
            with pytest.raises(InputRequiredRoundsExceededError):
                await client.call_tool("combine", {})


class TestLegacyUnaffected:
    async def test_legacy_call_tool_no_mrtr(self):
        """A legacy FastMCP server never emits InputRequiredResult; call_tool
        behaves byte-identically to pre-driver behavior."""
        mcp = FastMCP("legacy")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        async with Client(mcp, mode="legacy") as client:
            result = await client.call_tool("add", {"a": 2, "b": 3})
        assert result.data == 5
        assert result.is_error is False

    async def test_legacy_elicitation_still_synchronous(self):
        """On a legacy connection, a FastMCP server's `ctx.elicit` uses the
        synchronous server-to-client request path (not MRTR), and the same
        elicitation_handler answers it. This proves the handler table is shared."""
        from fastmcp import Context
        from fastmcp.server.elicitation import AcceptedElicitation

        mcp = FastMCP("legacy-elicit")

        @mcp.tool
        async def ask(ctx: Context) -> str:
            result = await ctx.elicit("your name", response_type=_Name)
            assert isinstance(result, AcceptedElicitation)
            data = cast(_Name, result.data)
            return f"Hi {data.name}"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content=response_type(name="Zoe"))

        async with Client(mcp, mode="legacy", elicitation_handler=handler) as client:
            result = await client.call_tool("ask", {})
        assert result.data == "Hi Zoe"
