"""Dual-era protocol matrix: one FastMCP server served over both MCP protocol eras.

FastMCP must serve the legacy (initialize-handshake, 2025-11-25) era and the
modern (server/discover, 2026-07-28) era from the same server object. The test
harness is the v2 SDK's own first-class client, ``mcp.client.Client``, which
resolves an in-process ``Server`` directly:

* ``mode='legacy'`` forces the initialize handshake (2025-11-25 in-memory).
* ``mode='auto'`` probes ``server/discover`` and negotiates 2026-07-28.
* ``mode='2026-07-28'`` pins the modern version and adopts a synthesized
  ``DiscoverResult`` (no probe).

A FastMCP server exposes its lowlevel ``Server`` as ``fastmcp_server._mcp_server``;
that is what we hand to the SDK client, mirroring how
``mcp.client._memory.InMemoryTransport`` unwraps servers.

Several cells characterize behavior that is a verified SDK-era contract rather
than a FastMCP choice; those are flagged inline and cross-referenced to the
migration feedback dossier (``<scratchpad>/specs/sdk-feedback.md``).
"""

from __future__ import annotations

import mcp_types as types
import pytest
from mcp.client import Client as SDKClient
from mcp.client.session import ClientRequestContext
from mcp.server import Server as LowLevelServer
from mcp.shared.exceptions import MCPError
from pydantic import FileUrl

from fastmcp import Client as FastMCPClient
from fastmcp import Context, FastMCP
from fastmcp.exceptions import PromptError, ResourceError
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp.server.middleware import Middleware

# Modes that reach the modern (2026-07-28) era via the SDK client.
MODERN_MODES = ["auto", "2026-07-28"]
# Both eras, for cells that must produce identical semantics on each.
ALL_MODES = ["legacy", *MODERN_MODES]


@pytest.fixture
def dual_era_server() -> FastMCP:
    """A single FastMCP server exercising every core MCP object type.

    Deliberately minimal and side-effect free so the same instance can be
    driven concurrently by legacy and modern clients within one test.
    """
    mcp = FastMCP("dual-era")

    @mcp.tool
    def add(a: int, b: int) -> int:
        """Structured-output tool (returns a scalar wrapped as {"result": ...})."""
        return a + b

    @mcp.resource("data://config")
    def config() -> dict:
        return {"version": 1}

    @mcp.resource("data://item/{item_id}")
    def item(item_id: str) -> str:
        return f"item-{item_id}"

    @mcp.prompt
    def summarize(topic: str) -> str:
        return f"Summarize {topic}"

    return mcp


def _server(mcp: FastMCP) -> LowLevelServer:
    """The lowlevel Server the SDK client connects to in-process."""
    return mcp._mcp_server


def _texts(blocks) -> list[str]:
    """Text from CallToolResult.content blocks (TextContent)."""
    return [b.text for b in blocks if isinstance(b, types.TextContent)]


def _resource_texts(blocks) -> list[str]:
    """Text from ReadResourceResult.contents blocks (TextResourceContents)."""
    return [b.text for b in blocks if isinstance(b, types.TextResourceContents)]


# ---------------------------------------------------------------------------
# 1. Core operations produce identical semantics on BOTH eras
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_list_tools_both_eras(dual_era_server, mode):
    async with SDKClient(_server(dual_era_server), mode=mode) as client:
        result = await client.list_tools()
    assert [t.name for t in result.tools] == ["add"]


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_call_tool_structured_output_both_eras(dual_era_server, mode):
    async with SDKClient(_server(dual_era_server), mode=mode) as client:
        result = await client.call_tool("add", {"a": 2, "b": 3})
    assert result.is_error is False
    assert result.structured_content == {"result": 5}
    assert _texts(result.content) == ["5"]


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_list_resources_both_eras(dual_era_server, mode):
    async with SDKClient(_server(dual_era_server), mode=mode) as client:
        result = await client.list_resources()
    assert [str(r.uri) for r in result.resources] == ["data://config"]


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_read_resource_both_eras(dual_era_server, mode):
    async with SDKClient(_server(dual_era_server), mode=mode) as client:
        result = await client.read_resource("data://config")
    assert _resource_texts(result.contents) == ['{"version": 1}']


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_read_resource_template_both_eras(dual_era_server, mode):
    async with SDKClient(_server(dual_era_server), mode=mode) as client:
        result = await client.read_resource("data://item/42")
    assert _resource_texts(result.contents) == ["item-42"]


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_list_prompts_both_eras(dual_era_server, mode):
    async with SDKClient(_server(dual_era_server), mode=mode) as client:
        result = await client.list_prompts()
    assert [p.name for p in result.prompts] == ["summarize"]


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_get_prompt_both_eras(dual_era_server, mode):
    async with SDKClient(_server(dual_era_server), mode=mode) as client:
        result = await client.get_prompt("summarize", {"topic": "cats"})
    rendered = [
        m.content.text
        for m in result.messages
        if isinstance(m.content, types.TextContent)
    ]
    assert rendered == ["Summarize cats"]


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_complete_parity_both_eras(dual_era_server, mode):
    """FastMCP registers no completion handler, so `completion/complete` is
    method-not-found. The point of this cell is parity: the *same* -32601
    surfaces on both eras (2026 did not change the unsupported-method contract).
    """
    async with SDKClient(_server(dual_era_server), mode=mode) as client:
        with pytest.raises(MCPError) as excinfo:
            await client.complete(
                types.PromptReference(name="summarize"),
                {"name": "topic", "value": "c"},
            )
    assert excinfo.value.code == types.METHOD_NOT_FOUND


# ---------------------------------------------------------------------------
# 2. Discovery / identity: which negotiation path each mode takes
# ---------------------------------------------------------------------------


async def test_legacy_uses_initialize_handshake(dual_era_server):
    """Legacy mode runs the initialize handshake and reports a handshake-era
    protocol version with server_info carried in the InitializeResult.
    """
    async with SDKClient(_server(dual_era_server), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        assert client.server_info is not None
        assert client.server_info.name == "dual-era"


async def test_auto_negotiates_modern_via_discover(dual_era_server):
    """`mode='auto'` probes server/discover and adopts 2026-07-28, populating
    server_info/capabilities from the DiscoverResult.
    """
    async with SDKClient(_server(dual_era_server), mode="auto") as client:
        assert client.protocol_version == "2026-07-28"
        # server/discover carries identity, unlike the synthesized pin below.
        assert client.server_info is not None
        assert client.server_info.name == "dual-era"
        assert client.server_capabilities is not None


async def test_pinned_modern_adopts_without_probe(dual_era_server):
    """Pinning `mode='2026-07-28'` adopts the version directly. With no
    `prior_discover`, the SDK synthesizes a minimal DiscoverResult that carries
    no identity, so server_info is absent even though the protocol version is
    modern.

    Characterization of the SDK's synthesize-discover path (mcp.client.client
    `_synthesize_discover`): a pin without prior_discover trades identity for
    skipping the probe round-trip.
    """
    async with SDKClient(_server(dual_era_server), mode="2026-07-28") as client:
        assert client.protocol_version == "2026-07-28"
        assert client.server_info is None


# ---------------------------------------------------------------------------
# 3. Push-feature degradation on 2026 vs. working callbacks on legacy
# ---------------------------------------------------------------------------


@pytest.fixture
def push_server() -> FastMCP:
    mcp = FastMCP("push")

    @mcp.tool
    async def do_elicit(ctx: Context) -> str:
        result = await ctx.elicit("pick a value", response_type=int)
        assert isinstance(result, AcceptedElicitation)
        return f"elicited {result.data}"

    @mcp.tool
    async def do_log(ctx: Context) -> str:
        await ctx.info("a log line")
        return "logged"

    return mcp


async def _accept_elicit(
    context: ClientRequestContext, params: types.ElicitRequestParams
) -> types.ElicitResult:
    return types.ElicitResult(action="accept", content={"value": 7})


async def _sampling_cb(
    context: ClientRequestContext, params: types.CreateMessageRequestParams
) -> types.CreateMessageResult:
    return types.CreateMessageResult(
        role="assistant",
        content=types.TextContent(type="text", text="sampled-text"),
        model="test-model",
    )


async def _roots_cb(context: ClientRequestContext) -> types.ListRootsResult:
    return types.ListRootsResult(
        roots=[types.Root(uri=FileUrl("file:///tmp"), name="tmp")]
    )


async def test_elicit_works_on_legacy(push_server):
    async with SDKClient(
        _server(push_server), mode="legacy", elicitation_callback=_accept_elicit
    ) as client:
        result = await client.call_tool("do_elicit", {})
    assert result.is_error is False
    assert _texts(result.content) == ["elicited 7"]


@pytest.mark.parametrize("mode", MODERN_MODES)
async def test_elicit_degrades_on_modern(push_server, mode):
    """Elicitation is a server-initiated request, removed at 2026-07-28
    (SEP-2577), so a tool that uses it must degrade to a surfaced error rather
    than hang or crash the connection. The connection survives: a subsequent
    normal call still works.
    """
    async with SDKClient(
        _server(push_server),
        mode=mode,
        elicitation_callback=_accept_elicit,
        sampling_callback=_sampling_cb,
        list_roots_callback=_roots_cb,
    ) as client:
        result = await client.call_tool("do_elicit", {})
        assert result.is_error is True
        log_result = await client.call_tool("do_log", {})
        assert log_result.is_error is False


async def test_elicit_degradation_message_is_clear_on_modern(push_server):
    """FastMCP era-gates elicit: on a 2026-07-28 connection it raises a clear,
    era-aware error before hitting the wire, instead of the SDK's opaque
    'Method not found' (sdk-feedback.md #10).
    """
    async with SDKClient(
        _server(push_server),
        mode="2026-07-28",
        elicitation_callback=_accept_elicit,
    ) as client:
        result = await client.call_tool("do_elicit", {})
    assert result.is_error is True
    assert "server-initiated" in " ".join(_texts(result.content)).lower()


# ---------------------------------------------------------------------------
# 3a-bis. Sampling and roots are not in the server API at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["sample", "sample_step", "list_roots"])
def test_removed_server_initiated_methods_are_absent(name):
    """FastMCP 4 targets the modern protocol, so the capabilities SEP-2577
    removed are not in the server-authoring API — not deprecated, not era-gated,
    absent. A server that calls them fails at attribute lookup, in every era.
    """
    assert not hasattr(Context, name)


@pytest.mark.parametrize("kwarg", ["sampling_handler", "sampling_handler_behavior"])
def test_server_sampling_handler_kwargs_are_rejected(kwarg):
    """The server-side sampling handler existed only to answer `ctx.sample()`."""
    with pytest.raises(TypeError, match="SEP-2577"):
        FastMCP("gone", **{kwarg: None})  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("mode", MODERN_MODES)
async def test_logging_notification_still_flows_on_modern(push_server, mode):
    """`ctx.info` is a server->client *notification*, not a request. Unlike the
    removed server-initiated requests, notifications still flow over the modern
    direct-dispatcher path, so the tool completes successfully.

    Characterization: current behavior is silent success (the log is emitted,
    the tool returns normally); we assert that rather than an error.
    """
    async with SDKClient(_server(push_server), mode=mode) as client:
        result = await client.call_tool("do_log", {})
    assert result.is_error is False
    assert _texts(result.content) == ["logged"]


# ---------------------------------------------------------------------------
# 5. Sessionless safety: session-id-keyed paths must not crash on 2026 in-memory
# ---------------------------------------------------------------------------


@pytest.fixture
def sessionless_server() -> FastMCP:
    mcp = FastMCP("sessionless")

    @mcp.tool
    async def read_session_id(ctx: Context) -> str:
        # In-memory/HTTP-less connections have no HTTP session id; FastMCP must
        # synthesize a stable one rather than raise.
        return ctx.session_id

    return mcp


@pytest.mark.parametrize("mode", MODERN_MODES)
async def test_session_id_access_does_not_crash_on_modern(sessionless_server, mode):
    async with SDKClient(_server(sessionless_server), mode=mode) as client:
        result = await client.call_tool("read_session_id", {})
    assert result.is_error is False
    # A non-empty synthesized id was returned.
    assert _texts(result.content)[0]


@pytest.mark.parametrize("mode", MODERN_MODES)
async def test_set_logging_level_is_era_gated_on_modern(sessionless_server, mode):
    """`logging/setLevel` asks a server to remember a level for the session, and
    the modern era has no session — the method is absent from its registry. The
    FastMCP client says so plainly instead of no-opping or surfacing the SDK's
    opaque "Method not found", and the connection stays usable afterward.
    """
    async with FastMCPClient(sessionless_server, mode=mode) as client:
        with pytest.raises(RuntimeError, match="2026-07-28"):
            await client.set_logging_level("debug")
        result = await client.call_tool("read_session_id", {})
    assert result.is_error is False


# ---------------------------------------------------------------------------
# 6. FastMCP middleware runs on both eras
# ---------------------------------------------------------------------------


class _CallToolCounter(Middleware):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    async def on_call_tool(self, context, call_next):
        self.count += 1
        return await call_next(context)


async def test_middleware_runs_on_both_eras():
    counter = _CallToolCounter()
    mcp = FastMCP("mw")
    mcp.add_middleware(counter)

    @mcp.tool
    def ping() -> str:
        return "pong"

    for mode in ("legacy", "2026-07-28"):
        async with SDKClient(mcp._mcp_server, mode=mode) as client:
            result = await client.call_tool("ping", {})
        assert result.is_error is False
        assert _texts(result.content) == ["pong"]

    # One invocation observed from each era.
    assert counter.count == 2


# ---------------------------------------------------------------------------
# Resource / prompt handler errors must survive both eras
# ---------------------------------------------------------------------------


@pytest.fixture
def erroring_server() -> FastMCP:
    """A server whose resource and prompt handlers raise FastMCP errors."""
    mcp = FastMCP("erroring")

    @mcp.resource("data://boom")
    def boom() -> str:
        raise ResourceError("resource detail marker")

    @mcp.resource("data://items/{item_id}")
    def item(item_id: int) -> str:
        return f"item {item_id}"

    @mcp.prompt
    def explode() -> str:
        raise PromptError("prompt detail marker")

    return mcp


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_resource_error_message_reaches_client(
    erroring_server: FastMCP, mode: str
) -> None:
    """A ResourceError's message must reach the wire on every era.

    The modern runner masks any handler exception that is not an MCPError or a
    ValidationError as a generic "Internal server error", so a ResourceError
    that escapes the handler becomes indistinguishable from a server bug.
    """
    async with FastMCPClient(erroring_server, mode=mode) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.read_resource("data://boom")

    assert "resource detail marker" in str(exc_info.value)


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_prompt_error_message_reaches_client(
    erroring_server: FastMCP, mode: str
) -> None:
    """A PromptError's message must reach the wire on every era."""
    async with FastMCPClient(erroring_server, mode=mode) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.get_prompt("explode")

    assert "prompt detail marker" in str(exc_info.value)


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_resource_template_conversion_error_reaches_client(
    erroring_server: FastMCP, mode: str
) -> None:
    """A bad template argument is a client-input error, not a server fault.

    This is the path that originally exposed the masking: converting
    ``item_id`` to an int fails, and the resulting error must name the problem
    rather than surface as a generic internal error.
    """
    async with FastMCPClient(erroring_server, mode=mode) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.read_resource("data://items/not-an-int")

    assert "Internal server error" not in str(exc_info.value)


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_resource_error_masked_when_masking_enabled(mode: str) -> None:
    """Masking still applies: resources leak no more than tools already do."""
    mcp = FastMCP("masked", mask_error_details=True)

    @mcp.resource("data://boom")
    def boom() -> str:
        raise ValueError("secret internal detail")

    async with FastMCPClient(mcp, mode=mode) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.read_resource("data://boom")

    assert "secret internal detail" not in str(exc_info.value)


@pytest.mark.parametrize("mode", ALL_MODES)
async def test_prompt_error_masked_when_masking_enabled(mode: str) -> None:
    """Masking still applies: prompts leak no more than tools already do."""
    mcp = FastMCP("masked", mask_error_details=True)

    @mcp.prompt
    def render(topic: str) -> str:
        raise RuntimeError("secret internal detail")

    async with FastMCPClient(mcp, mode=mode) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.get_prompt("render", {"topic": "x"})

    assert "secret internal detail" not in str(exc_info.value)
