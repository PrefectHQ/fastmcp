"""Tests for surfacing SEP-2133 client extensions on ``fastmcp.Client``.

Covers that ``extensions=`` / ``result_claims=`` are folded into the underlying
``ClientSession`` kwargs on construction, that user-supplied notification
bindings *compose* with FastMCP's internal task-status binding rather than
clobbering it, that both bindings actually fire against a live server, and that
a claimed ``tools/call`` result is resolved end-to-end through the owning
extension's resolver.
"""

import asyncio
from typing import Any, Literal

import pytest
from mcp.client.extension import (
    ClaimContext,
    ClientExtension,
    NotificationBinding,
    ResultClaim,
    UnexpectedClaimedResult,
)
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.extension import Extension
from mcp.server.mcpserver import MCPServer as SDKServer
from mcp_types import CallToolRequestParams, CallToolResult, Result, TextContent
from mcp_types.version import LATEST_MODERN_VERSION
from pydantic import BaseModel

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.dependencies import get_context

CUSTOM_METHOD = "notifications/x-test/ping"
TASK_STATUS_METHOD = "notifications/tasks/status"
EXTENSION_ID = "test.example.com/demo"
CLAIMED_TYPE = "x-test/claimed"


class PingParams(BaseModel):
    value: int = 0


class ClaimedResult(Result):
    result_type: Literal["x-test/claimed"]
    payload: str = ""


async def _resolve_claimed(result: ClaimedResult, ctx: ClaimContext) -> CallToolResult:
    """Finish a claimed result into an ordinary CallToolResult.

    Echoes the claimed payload so a test can prove the resolver ran on the
    server-emitted value rather than a placeholder.
    """
    return CallToolResult(
        content=[TextContent(type="text", text=f"resolved:{result.payload}")]
    )


def _make_claim() -> ResultClaim[ClaimedResult]:
    return ResultClaim(
        result_type=CLAIMED_TYPE,
        model=ClaimedResult,
        resolve=_resolve_claimed,
    )


class _DemoExtension(ClientExtension):
    """Extension contributing a settings ad, a result claim, and a binding."""

    identifier = EXTENSION_ID

    def __init__(self, received: list[PingParams] | None = None) -> None:
        self._received = received if received is not None else []

    def settings(self) -> dict[str, Any]:
        return {"enabled": True}

    def claims(self):
        return (_make_claim(),)

    def notifications(self):
        async def _handler(params: PingParams) -> None:
            self._received.append(params)

        return (
            NotificationBinding(
                method=CUSTOM_METHOD,
                params_type=PingParams,
                handler=_handler,
            ),
        )


class _ServerClaimExtension(Extension):
    """Server-side extension that answers a specific tool with a claimed shape."""

    identifier = EXTENSION_ID

    async def intercept_tool_call(
        self,
        params: CallToolRequestParams,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        if params.name == "claimed_tool":
            return ClaimedResult(result_type=CLAIMED_TYPE, payload="from-server")
        return await call_next(ctx)


def _claiming_server() -> SDKServer:
    """An SDK MCPServer whose `claimed_tool` returns a claimed extension result."""
    server = SDKServer("claim-server", extensions=[_ServerClaimExtension()])

    # No return annotation → no output schema, so the resolved CallToolResult
    # (plain text, no structured content) passes revalidation.
    @server.tool()
    def claimed_tool():
        return None

    return server


def _binding_methods(client: Client) -> list[str]:
    bindings = client._session_kwargs.get("notification_bindings") or []
    return [b.method for b in bindings]


def test_extension_folds_into_session_kwargs():
    """A ClientExtension's ad, claim, and binding reach the session kwargs."""
    client = Client(FastMCP("srv"), extensions=[_DemoExtension()])

    assert client._session_kwargs.get("extensions") == {EXTENSION_ID: {"enabled": True}}
    result_claims = client._session_kwargs.get("result_claims")
    assert result_claims is not None
    assert [c.result_type for c in result_claims[EXTENSION_ID]] == [CLAIMED_TYPE]


def test_extension_populates_claim_by_model_index():
    """The claim is indexed by its model so the resolution path can find it."""
    client = Client(FastMCP("srv"), extensions=[_DemoExtension()])

    assert client._claim_by_model[ClaimedResult].result_type == CLAIMED_TYPE


def test_binding_composes_with_internal_task_binding():
    """User binding is appended to (not replacing) the task-status binding."""
    client = Client(FastMCP("srv"), extensions=[_DemoExtension()])

    methods = _binding_methods(client)
    assert TASK_STATUS_METHOD in methods
    assert CUSTOM_METHOD in methods
    # The internal task binding must lead so user bindings extend it.
    assert methods[0] == TASK_STATUS_METHOD


def test_no_extensions_leaves_only_task_binding():
    """Without extensions, only the internal task-status binding is registered."""
    client = Client(FastMCP("srv"))

    assert _binding_methods(client) == [TASK_STATUS_METHOD]
    assert "extensions" not in client._session_kwargs
    assert "result_claims" not in client._session_kwargs
    assert client._claim_by_model == {}


def test_new_preserves_extension_composition():
    """new() rebuilds the clone with both the task binding and user bindings."""
    client = Client(FastMCP("srv"), extensions=[_DemoExtension()])
    clone = client.new()

    methods = _binding_methods(clone)
    assert methods[0] == TASK_STATUS_METHOD
    assert CUSTOM_METHOD in methods
    assert clone._session_kwargs.get("extensions") == {EXTENSION_ID: {"enabled": True}}
    assert clone._claim_by_model[ClaimedResult].result_type == CLAIMED_TYPE


def test_result_claims_merge_with_extension_claims():
    """Explicit result_claims merge with an advertised extension's own claims."""

    class ExtraClaimed(Result):
        result_type: Literal["x-test/extra"]

    async def _resolve_extra(result: ExtraClaimed, ctx: ClaimContext) -> CallToolResult:
        return CallToolResult(content=[])

    extra_claim = ResultClaim(
        result_type="x-test/extra",
        model=ExtraClaimed,
        resolve=_resolve_extra,
    )

    client = Client(
        FastMCP("srv"),
        extensions=[_DemoExtension()],
        result_claims={EXTENSION_ID: [extra_claim]},
    )

    result_claims = client._session_kwargs.get("result_claims")
    assert result_claims is not None
    tags = {c.result_type for c in result_claims[EXTENSION_ID]}
    assert tags == {CLAIMED_TYPE, "x-test/extra"}
    # Both the extension claim and the explicit extra claim are resolvable.
    assert set(client._claim_by_model) == {ClaimedResult, ExtraClaimed}


async def test_user_binding_clobbering_task_method_is_rejected():
    """A user extension binding the task-status method cannot silently replace it.

    Composition means the internal task binding always leads; a user extension
    that binds the same method collides with it, and the SDK session rejects the
    duplicate at connect time rather than letting one silently win.
    """

    class TaskClobberExtension(ClientExtension):
        identifier = "test.example.com/clobber"

        def notifications(self):
            async def _handler(params: PingParams) -> None:
                return None

            return (
                NotificationBinding(
                    method=TASK_STATUS_METHOD,
                    params_type=PingParams,
                    handler=_handler,
                ),
            )

    client = Client(FastMCP("srv"), extensions=[TaskClobberExtension()])
    with pytest.raises(RuntimeError, match="duplicate notification binding"):
        async with client:
            pass


async def test_both_bindings_fire_against_live_server():
    """The internal task binding and a user extension binding both fire.

    A ``task=True`` tool drives ``notifications/tasks/status`` (the internal
    binding) while a second tool emits a custom notification the user extension
    observes, proving the two coexist on one live connection. Pinned to
    ``mode="legacy"`` because FastMCP task submission is a legacy-era feature.
    """
    received: list[PingParams] = []
    mcp = FastMCP("compose-server")

    @mcp.tool
    async def emit(value: int) -> int:
        ctx = get_context()
        # Emit a custom (non-core) notification straight onto the outbound
        # channel; unknown methods route to the client's notification bindings.
        await ctx.session._connection.notify(CUSTOM_METHOD, {"value": value})
        return value

    @mcp.tool(task=True)
    async def background(value: int) -> int:
        await asyncio.sleep(0.02)
        return value * 2

    client = Client(mcp, extensions=[_DemoExtension(received)], mode="legacy")

    async with client:
        # The user extension binding fires on the custom notification.
        await client.call_tool("emit", {"value": 21})
        # The internal task binding fires on the task-status notification.
        task = await client.call_tool("background", {"value": 5}, task=True)
        status = await task.wait(timeout=2.0)
        # Give the custom-notification queue a moment to drain.
        await asyncio.sleep(0.1)

    # Internal task binding fired: the task completed via a status notification.
    assert status.status == "completed"
    # User extension binding fired: it observed the custom notification.
    assert [p.value for p in received] == [21]


class TestClaimedResultResolution:
    """End-to-end resolution of a server-emitted claimed `tools/call` result."""

    @pytest.mark.parametrize("mode", ["auto", LATEST_MODERN_VERSION])
    async def test_call_tool_mcp_resolves_claimed_result(self, mode):
        """`call_tool_mcp` resolves a claimed result through the extension resolver.

        The server emits a claimed shape; the client's registered extension
        parses it and its resolver finishes it into an ordinary CallToolResult.
        Both the negotiated (`auto`) and pinned modern eras admit the claim.
        """
        client = Client(_claiming_server(), extensions=[_DemoExtension()], mode=mode)
        async with client:
            assert client.protocol_version == LATEST_MODERN_VERSION
            result = await client.call_tool_mcp("claimed_tool", {})

        block = result.content[0]
        assert isinstance(block, TextContent)
        assert block.text == "resolved:from-server"

    async def test_call_tool_resolves_claimed_result(self):
        """The high-level `call_tool` also returns the resolver's CallToolResult."""
        client = Client(
            _claiming_server(),
            extensions=[_DemoExtension()],
            mode=LATEST_MODERN_VERSION,
        )
        async with client:
            parsed = await client.call_tool("claimed_tool", {})

        block = parsed.content[0]
        assert isinstance(block, TextContent)
        assert block.text == "resolved:from-server"

    async def test_unwired_session_call_raises_unexpected_claimed(self):
        """Regression guard for the half-wired bug: the raw session path raises.

        With the claim registered, calling `session.call_tool` directly (FastMCP's
        old tool path, which omitted `allow_claimed=True`) surfaces the claimed
        result as `UnexpectedClaimedResult` — the exact failure the wired
        `call_tool_mcp` path now avoids by resolving instead.
        """
        client = Client(
            _claiming_server(),
            extensions=[_DemoExtension()],
            mode=LATEST_MODERN_VERSION,
        )
        async with client:
            with pytest.raises(UnexpectedClaimedResult):
                await client.session.call_tool("claimed_tool", {})

            # The wired path resolves the very same claimed result.
            resolved = await client.call_tool_mcp("claimed_tool", {})
        block = resolved.content[0]
        assert isinstance(block, TextContent)
        assert block.text == "resolved:from-server"
