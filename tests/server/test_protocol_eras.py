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
from mcp_types import methods
from mcp_types.version import (
    HANDSHAKE_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSIONS,
)
from pydantic import FileUrl

from fastmcp import Client as FastMCPClient
from fastmcp import Context, FastMCP, settings
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
        assert client.server_info.name == "dual-era"


async def test_auto_negotiates_modern_via_discover(dual_era_server):
    """`mode='auto'` probes server/discover and adopts 2026-07-28, populating
    server_info/capabilities from the DiscoverResult.
    """
    async with SDKClient(_server(dual_era_server), mode="auto") as client:
        assert client.protocol_version == "2026-07-28"
        # server/discover carries identity, unlike the synthesized pin below.
        assert client.server_info.name == "dual-era"
        assert client.server_capabilities is not None


async def test_pinned_modern_adopts_without_probe(dual_era_server):
    """Pinning `mode='2026-07-28'` adopts the version directly. With no
    `prior_discover`, the SDK synthesizes a minimal DiscoverResult, so
    server_info is empty even though the protocol version is modern.

    Characterization of the SDK's synthesize-discover path (mcp.client.client
    `_synthesize_discover`): a pin without prior_discover trades identity for
    skipping the probe round-trip.
    """
    async with SDKClient(_server(dual_era_server), mode="2026-07-28") as client:
        assert client.protocol_version == "2026-07-28"
        assert client.server_info.name == ""


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
    async def do_sample(ctx: Context) -> str:
        result = await ctx.sample("hello")
        return f"sampled {result.text}"

    @mcp.tool
    async def do_list_roots(ctx: Context) -> str:
        roots = await ctx.list_roots()
        return f"roots {[str(r.uri) for r in roots]}"

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


async def test_sample_works_on_legacy(push_server):
    async with SDKClient(
        _server(push_server), mode="legacy", sampling_callback=_sampling_cb
    ) as client:
        result = await client.call_tool("do_sample", {})
    assert result.is_error is False
    assert _texts(result.content) == ["sampled sampled-text"]


async def test_list_roots_works_on_legacy(push_server):
    async with SDKClient(
        _server(push_server), mode="legacy", list_roots_callback=_roots_cb
    ) as client:
        result = await client.call_tool("do_list_roots", {})
    assert result.is_error is False
    assert _texts(result.content) == ["roots ['file:///tmp']"]


@pytest.mark.parametrize("mode", MODERN_MODES)
@pytest.mark.parametrize("tool", ["do_elicit", "do_sample", "do_list_roots"])
async def test_push_features_degrade_on_modern(push_server, mode, tool):
    """Server-initiated requests (elicitation/sampling/roots) are removed at
    2026-07-28 (SEP-2577), so a tool that uses them must degrade to a surfaced
    error rather than hang or crash the connection. This asserts the
    degradation happens and reaches the caller as an isError result.
    """
    async with SDKClient(
        _server(push_server),
        mode=mode,
        elicitation_callback=_accept_elicit,
        sampling_callback=_sampling_cb,
        list_roots_callback=_roots_cb,
    ) as client:
        result = await client.call_tool(tool, {})
        assert result.is_error is True
        # A subsequent normal call still works: the connection survived the
        # per-request failure rather than tearing down the whole session.
        log_result = await client.call_tool("do_log", {})
        assert log_result.is_error is False


async def test_list_roots_degradation_message_is_clear_on_modern(push_server):
    """`ctx.list_roots()` sends with no related_request_id, so the SDK selects
    the connection's no-back-channel outbound and raises the self-explanatory
    NoBackChannelError. This is the *good* degradation message and we assert it.
    """
    async with SDKClient(_server(push_server), mode="2026-07-28") as client:
        result = await client.call_tool("do_list_roots", {})
    assert result.is_error is True
    message = " ".join(_texts(result.content)).lower()
    assert "back-channel" in message and "server-initiated" in message


@pytest.mark.parametrize("tool", ["do_elicit", "do_sample"])
async def test_elicit_sample_degradation_message_is_clear_on_modern(push_server, tool):
    """FastMCP era-gates elicit/sample: on a 2026-07-28 connection they raise a
    clear, era-aware error before hitting the wire, instead of the SDK's opaque
    'Method not found' (sdk-feedback.md #10). Both messages name the removed
    server-initiated capability so the caller knows why the request degraded.
    """
    async with SDKClient(
        _server(push_server),
        mode="2026-07-28",
        elicitation_callback=_accept_elicit,
        sampling_callback=_sampling_cb,
    ) as client:
        result = await client.call_tool(tool, {})
    assert result.is_error is True
    message = " ".join(_texts(result.content)).lower()
    assert "server-initiated" in message


# ---------------------------------------------------------------------------
# 3a-bis. Server-configured sampling handler answers WITHOUT the client
# back-channel, so ctx.sample()/ctx.sample_step() must keep working on modern
# connections. The era-gate only fires when nothing can serve the request.
# ---------------------------------------------------------------------------


def _handler_server(behavior) -> FastMCP:
    """A server whose sampling is answered by a server-side handler."""

    def sampling_handler(messages, params, ctx) -> str:
        return "handler-answer"

    mcp = FastMCP("handler", sampling_handler=sampling_handler)
    if behavior is not None:
        mcp.sampling_handler_behavior = behavior

    @mcp.tool
    async def do_sample(ctx: Context) -> str:
        result = await ctx.sample("hello")
        return f"sampled {result.text}"

    @mcp.tool
    async def do_sample_step(ctx: Context) -> str:
        step = await ctx.sample_step("hello")
        return f"stepped {step.text}"

    return mcp


@pytest.mark.parametrize("mode", MODERN_MODES)
@pytest.mark.parametrize("behavior", ["always", "fallback"])
@pytest.mark.parametrize("method", ["do_sample", "do_sample_step"])
async def test_server_sampling_handler_works_on_modern(mode, behavior, method):
    """A server-side sampling handler answers entirely server-side, so it works
    on modern (2026-07-28) connections regardless of behavior. The era-gate must
    NOT block these — nothing touches the removed client back-channel. Crucially,
    'fallback' must go straight to the handler (no bare client-attempt failure)."""
    server = _handler_server(behavior)
    async with SDKClient(_server(server), mode=mode) as client:
        result = await client.call_tool(method, {})
    assert result.is_error is False
    assert "handler-answer" in " ".join(_texts(result.content))


@pytest.mark.parametrize("behavior", ["always", "fallback"])
@pytest.mark.parametrize("method", ["do_sample", "do_sample_step"])
async def test_server_sampling_handler_works_on_legacy(behavior, method):
    """Handshake-era behavior is unchanged: the server-side handler still answers
    on legacy connections."""
    server = _handler_server(behavior)
    async with SDKClient(_server(server), mode="legacy") as client:
        result = await client.call_tool(method, {})
    assert result.is_error is False
    assert "handler-answer" in " ".join(_texts(result.content))


@pytest.mark.parametrize("mode", MODERN_MODES)
@pytest.mark.parametrize("method", ["do_sample", "do_sample_step"])
async def test_sampling_without_handler_still_era_gated_on_modern(
    push_server, mode, method
):
    """With no server-side handler configured, the request would hit the removed
    client back-channel, so the clear era error still fires on modern."""
    # push_server only defines do_sample; add a do_sample_step twin inline.
    mcp = FastMCP("no-handler")

    @mcp.tool
    async def do_sample(ctx: Context) -> str:
        result = await ctx.sample("hello")
        return f"sampled {result.text}"

    @mcp.tool
    async def do_sample_step(ctx: Context) -> str:
        step = await ctx.sample_step("hello")
        return f"stepped {step.text}"

    async with SDKClient(
        _server(mcp), mode=mode, sampling_callback=_sampling_cb
    ) as client:
        result = await client.call_tool(method, {})
    assert result.is_error is True
    assert "server-initiated" in " ".join(_texts(result.content)).lower()


# ---------------------------------------------------------------------------
# 3b. Sampling deprecation warning (SEP-2577): ctx.sample/ctx.sample_step warn
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_sample_warn_flag():
    """Reset the process-wide warn-once flag so a warning can be observed."""
    import fastmcp.server.context as context_module

    original = set(context_module._sample_deprecation_warned)
    context_module._sample_deprecation_warned.clear()
    try:
        yield
    finally:
        context_module._sample_deprecation_warned.clear()
        context_module._sample_deprecation_warned.update(original)


@pytest.mark.parametrize("method", ["do_sample", "do_sample_step"])
async def test_sampling_emits_deprecation_warning(reset_sample_warn_flag, method):
    """`ctx.sample()` and `ctx.sample_step()` emit a FastMCPDeprecationWarning
    naming SEP-2577 and the server-side-LLM migration path."""
    from fastmcp.exceptions import FastMCPDeprecationWarning

    mcp = FastMCP("warn")

    @mcp.tool
    async def do_sample(ctx: Context) -> str:
        await ctx.sample("hello")
        return "ok"

    @mcp.tool
    async def do_sample_step(ctx: Context) -> str:
        await ctx.sample_step("hello")
        return "ok"

    with pytest.warns(FastMCPDeprecationWarning, match="SEP-2577"):
        async with SDKClient(
            _server(mcp), mode="legacy", sampling_callback=_sampling_cb
        ) as client:
            await client.call_tool(method, {})


async def test_sampling_deprecation_warning_fires_once_per_process(
    reset_sample_warn_flag,
):
    """The deprecation warning is warn-once: a second sample call in the same
    process does not re-warn."""
    from fastmcp.exceptions import FastMCPDeprecationWarning

    mcp = FastMCP("warn-once")

    @mcp.tool
    async def do_sample(ctx: Context) -> str:
        await ctx.sample("hello")
        return "ok"

    with pytest.warns(FastMCPDeprecationWarning):
        async with SDKClient(
            _server(mcp), mode="legacy", sampling_callback=_sampling_cb
        ) as client:
            await client.call_tool("do_sample", {})

    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", FastMCPDeprecationWarning)
        async with SDKClient(
            _server(mcp), mode="legacy", sampling_callback=_sampling_cb
        ) as client:
            result = await client.call_tool("do_sample", {})
    assert result.is_error is False


async def test_sampling_deprecation_warning_suppressible_via_settings(
    reset_sample_warn_flag, monkeypatch
):
    """Setting `deprecation_warnings=False` suppresses the sampling warning,
    matching the house pattern for every other FastMCP deprecation."""
    import warnings as _warnings

    from fastmcp.exceptions import FastMCPDeprecationWarning

    monkeypatch.setattr(settings, "deprecation_warnings", False)

    mcp = FastMCP("no-warn")

    @mcp.tool
    async def do_sample(ctx: Context) -> str:
        await ctx.sample("hello")
        return "ok"

    with _warnings.catch_warnings():
        _warnings.simplefilter("error", FastMCPDeprecationWarning)
        async with SDKClient(
            _server(mcp), mode="legacy", sampling_callback=_sampling_cb
        ) as client:
            result = await client.call_tool("do_sample", {})
    assert result.is_error is False


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
# 4. Tasks: submission + tasks/get across the eras the _sdk_patches shim covers
# ---------------------------------------------------------------------------


@pytest.fixture
def task_server() -> FastMCP:
    mcp = FastMCP("tasks")

    @mcp.tool(task=True)
    async def slow_add(a: int, b: int) -> int:
        return a + b

    return mcp


async def test_task_submission_and_get_on_legacy_latest(task_server):
    """Legacy-latest (2025-11-25): a task-augmented tools/call returns a
    CreateTaskResult and tasks/get resolves it. This exercises the
    _sdk_patches registry-widening shim at the 2025-11-25 tools/call surface.

    Driven with the FastMCP client because the v2 SDK client's call_tool has no
    `task=` parameter (verified: mcp.client.session.ClientSession.call_tool
    exposes no task metadata arg) — see item below.
    """
    async with FastMCPClient(task_server) as client:
        assert client.initialize_result is not None
        assert client.initialize_result.protocol_version == "2025-11-25"

        task = await client.call_tool("slow_add", {"a": 2, "b": 3}, task=True)
        assert task.task_id
        assert not task.returned_immediately

        await task.wait(timeout=3.0)
        result = await task.result()
        assert result.data == 5


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The v2 SDK high-level client (mcp.client.Client) and ClientSession "
        "expose no `task=` parameter on call_tool, so a task-augmented "
        "tools/call cannot be submitted through it at any era; a hand-built "
        "raw CallToolRequest does not drive FastMCP's task path either. On "
        "2026-07-28 tasks moved to the io.modelcontextprotocol/tasks extension "
        "and CreateTaskResult is not part of the tools/call union, so the "
        "_sdk_patches shim intentionally does not widen the modern row "
        "(sdk-feedback.md #1). Remove once the SDK client supports task "
        "submission."
    ),
)
async def test_task_submission_on_modern(task_server):
    async with SDKClient(_server(task_server), mode="2026-07-28") as client:
        params = types.CallToolRequestParams(
            name="slow_add",
            arguments={"a": 1, "b": 2},
            task=types.TaskMetadata(ttl=60000),
        )
        result = await client.session.send_request(
            types.CallToolRequest(params=params), types.CreateTaskResult
        )
        assert isinstance(result, types.CreateTaskResult)


# ---------------------------------------------------------------------------
# 4b. _sdk_patches registry gating: the SEP-1686 task shim widens ONLY the
# handshake-era rows and leaves the 2026-07-28 (extension-era) rows untouched.
# ---------------------------------------------------------------------------


def test_task_shim_widens_handshake_tools_call_rows():
    """Every handshake-era tools/call row gains a CreateTaskResult arm."""
    from fastmcp._sdk_patches import get_union_arms

    for version in HANDSHAKE_PROTOCOL_VERSIONS:
        row = methods.SERVER_RESULTS[("tools/call", version)]
        assert types.CreateTaskResult in get_union_arms(row), version


def test_task_shim_does_not_touch_modern_tools_call_row():
    """The 2026-07-28 tools/call row stays the unpatched MRTR union: tasks are
    the io.modelcontextprotocol/tasks extension there, so CreateTaskResult must
    not be injected."""
    from fastmcp._sdk_patches import get_union_arms

    row = methods.SERVER_RESULTS[("tools/call", "2026-07-28")]
    arms = get_union_arms(row)
    assert types.CreateTaskResult not in arms
    # Unchanged from the SDK default: the 2026 mutually-recursive tool result
    # (CallToolResult | InputRequiredResult), keyed by the version-specific types.
    arm_names = {arm.__name__ for arm in arms}
    assert arm_names == {"CallToolResult", "InputRequiredResult"}


@pytest.mark.parametrize(
    "task_method",
    ["tasks/get", "tasks/result", "tasks/list", "tasks/cancel"],
)
def test_task_shim_registers_tasks_rows_only_for_handshake_eras(task_method):
    """tasks/* result rows exist for handshake-era versions and are absent for
    the modern (extension) era."""
    for version in HANDSHAKE_PROTOCOL_VERSIONS:
        assert (task_method, version) in methods.SERVER_RESULTS, (task_method, version)
    for version in MODERN_PROTOCOL_VERSIONS:
        assert (task_method, version) not in methods.SERVER_RESULTS, (
            task_method,
            version,
        )


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
async def test_set_logging_level_does_not_crash_on_modern(sessionless_server, mode):
    """logging/setLevel is a session-id-keyed, deprecated-at-2026 operation.
    On a sessionless modern in-memory connection it must degrade cleanly (either
    succeed as a no-op or raise a surfaced MCPError) rather than crash the
    connection. Characterization: capture whichever the current contract is.
    """
    async with SDKClient(_server(sessionless_server), mode=mode) as client:
        outcome: str
        try:
            await client.set_logging_level("debug")  # ty: ignore[deprecated]
            outcome = "ok"
        except MCPError:
            outcome = "mcperror"
        # Either way the connection is still usable afterward.
        result = await client.call_tool("read_session_id", {})
    assert result.is_error is False
    assert outcome in {"ok", "mcperror"}


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
