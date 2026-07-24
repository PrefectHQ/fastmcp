"""Tests for surfacing SEP-2133 client extensions on ``fastmcp.Client``.

Covers that ``extensions=`` / ``result_claims=`` are folded into the underlying
``ClientSession`` kwargs on construction, that a claimed ``tools/call`` result is
resolved end-to-end through the owning extension's resolver, and that FastMCP's
internal tasks extension (from ``fastmcp-tasks``, imported below) is folded in
automatically and *composes* with a user's own extensions rather than being
clobbered by them.

Importing ``fastmcp_tasks`` registers the internal client extension factory
process-wide, so every ``Client`` built here carries the tasks capability ad and
its ``resultType: "task"`` claim. These tests assert that composition explicitly.
"""

from typing import Any, Literal

import pytest
from fastmcp_tasks.client_models import ClientCreateTaskResult
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

# Importing the package registers the internal tasks client extension factory, so
# every Client below folds the tasks extension in. Kept as an explicit import so
# the composition assertions are deterministic regardless of test import order.
import fastmcp_tasks  # noqa: F401
from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.utilities.tasks import TASKS_EXTENSION_ID

CUSTOM_METHOD = "notifications/x-test/ping"
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


def test_extension_folds_into_session_kwargs():
    """A ClientExtension's ad and claim reach the session kwargs, alongside tasks."""
    client = Client(FastMCP("srv"), extensions=[_DemoExtension()])

    # The tasks extension is auto-folded in beside the user's own.
    assert client._session_kwargs.get("extensions") == {
        TASKS_EXTENSION_ID: {},
        EXTENSION_ID: {"enabled": True},
    }
    result_claims = client._session_kwargs.get("result_claims")
    assert result_claims is not None
    assert [c.result_type for c in result_claims[EXTENSION_ID]] == [CLAIMED_TYPE]
    assert [c.result_type for c in result_claims[TASKS_EXTENSION_ID]] == ["task"]


def test_extension_populates_claim_by_model_index():
    """The claim is indexed by its model so the resolution path can find it."""
    client = Client(FastMCP("srv"), extensions=[_DemoExtension()])

    assert client._claim_by_model[ClaimedResult].result_type == CLAIMED_TYPE
    # The auto-folded tasks claim is indexed too.
    assert client._claim_by_model[ClientCreateTaskResult].result_type == "task"


def test_internal_tasks_extension_present_without_user_extensions():
    """Even with no user extensions, the tasks claim is auto-registered."""
    client = Client(FastMCP("srv"))

    assert client._session_kwargs.get("extensions") == {TASKS_EXTENSION_ID: {}}
    assert client._claim_by_model[ClientCreateTaskResult].result_type == "task"


def test_user_extension_composes_with_internal_tasks_extension():
    """A user extension is folded in beside the internal tasks extension."""
    client = Client(FastMCP("srv"), extensions=[_DemoExtension()])

    ad = client._session_kwargs.get("extensions") or {}
    assert TASKS_EXTENSION_ID in ad
    assert EXTENSION_ID in ad
    # Both claims are resolvable.
    assert set(client._claim_by_model) == {ClaimedResult, ClientCreateTaskResult}


def test_user_extension_may_override_internal_tasks_extension():
    """A user extension declaring the tasks identifier wins; the internal one drops.

    Composition prefers the user's extension: rather than colliding on the shared
    identifier (which the fold rejects), the internal tasks extension is dropped so
    a power user can supply their own task-handling extension.
    """

    class CustomTasks(ClientExtension):
        identifier = TASKS_EXTENSION_ID

        def settings(self) -> dict[str, Any]:
            return {"custom": True}

    client = Client(FastMCP("srv"), extensions=[CustomTasks()])

    assert client._session_kwargs.get("extensions") == {
        TASKS_EXTENSION_ID: {"custom": True}
    }
    # The user extension declares no claim, so no task claim is registered.
    assert client._claim_by_model == {}


def test_new_preserves_extension_composition():
    """new() rebuilds the clone with both the tasks extension and user extensions."""
    client = Client(FastMCP("srv"), extensions=[_DemoExtension()])
    clone = client.new()

    ad = clone._session_kwargs.get("extensions") or {}
    assert TASKS_EXTENSION_ID in ad
    assert EXTENSION_ID in ad
    assert clone._claim_by_model[ClaimedResult].result_type == CLAIMED_TYPE
    assert clone._claim_by_model[ClientCreateTaskResult].result_type == "task"


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
    # The extension claim, the explicit extra claim, and the tasks claim resolve.
    assert set(client._claim_by_model) == {
        ClaimedResult,
        ExtraClaimed,
        ClientCreateTaskResult,
    }


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
