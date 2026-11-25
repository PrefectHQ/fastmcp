"""A proxy relays server-initiated input only for features the front client declared.

A backend that asks the user for input falls back when its client cannot
answer, so the proxy's backend client must not claim a capability the front
client lacks. A handshake-era front client also has no `InputRequiredResult`,
so a backend pinned to the modern era has its ask relayed as an ordinary
`elicitation/create` request instead.
"""

from dataclasses import dataclass
from typing import Any

import mcp.types
import pytest
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    InputRequiredResult,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import (
    FastMCPProxy,
    ProxyClient,
    StatefulProxyClient,
)

FALLBACK = "ask the user in chat"
CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


@dataclass
class Confirmation:
    ok: bool


def _ask_to_confirm() -> InputRequiredResult:
    params = ElicitRequestFormParams(
        message="Delete everything?", requested_schema=CONFIRM_SCHEMA
    )
    return InputRequiredResult(
        result_type="input_required",
        input_requests={
            "confirm": ElicitRequest(method="elicitation/create", params=params)
        },
    )


async def _confirm(ctx: Context) -> str | InputRequiredResult:
    """Confirm with the user on either era, falling back when it can't elicit."""
    capabilities = ctx.session.client_capabilities
    if capabilities is None or capabilities.elicitation is None:
        return FALLBACK
    if ctx.session.protocol_version in MODERN_PROTOCOL_VERSIONS:
        if ctx.input_responses is None:
            return _ask_to_confirm()
        answer = ctx.input_responses["confirm"]
        assert isinstance(answer, mcp.types.ElicitResult)
        return f"confirmed: {answer.action}"
    result = await ctx.elicit("Delete everything?", Confirmation)
    return f"confirmed: {result.action}"


@pytest.fixture
def backend() -> FastMCP:
    mcp = FastMCP("Backend")

    @mcp.tool
    async def confirm_delete(ctx: Context) -> str | InputRequiredResult:
        return await _confirm(ctx)

    @mcp.resource("data://confirm")
    async def confirm_resource(ctx: Context) -> str | InputRequiredResult:
        return await _confirm(ctx)

    @mcp.resource("data://confirm/{item}")
    async def confirm_template(item: str, ctx: Context) -> str | InputRequiredResult:
        return await _confirm(ctx)

    @mcp.prompt
    async def confirm_prompt(ctx: Context) -> str | InputRequiredResult:
        return await _confirm(ctx)

    @mcp.tool
    def declared_capabilities(ctx: Context) -> list[str]:
        capabilities = ctx.session.client_capabilities
        if capabilities is None:
            return []
        return sorted(
            name
            for name in ("elicitation", "roots", "sampling")
            if getattr(capabilities, name) is not None
        )

    return mcp


async def accept(
    message: str, response_type: Any, params: Any, context: Any
) -> ElicitResult:
    return ElicitResult(action="accept", content={"ok": True})


def _front_client(server: FastMCP, mode: str, elicit: bool) -> Client:
    if elicit:
        return Client(server, mode=mode, elicitation_handler=accept)
    return Client(server, mode=mode)


@pytest.mark.parametrize("backend_mode", [None, "auto"], ids=["mirrored", "pinned"])
@pytest.mark.parametrize("front_mode", ["legacy", "auto"])
@pytest.mark.parametrize(
    ("elicit", "expected"), [(True, "confirmed: accept"), (False, FALLBACK)]
)
async def test_tool_input_reaches_front_client_or_falls_back(
    backend: FastMCP,
    backend_mode: str | None,
    front_mode: str,
    elicit: bool,
    expected: str,
):
    proxy = create_proxy(backend, mode=backend_mode)
    async with _front_client(proxy, front_mode, elicit) as client:
        result = await client.call_tool("confirm_delete", {})
    assert result.data == expected


@pytest.mark.parametrize(
    ("elicit", "expected"), [(True, "confirmed: accept"), (False, FALLBACK)]
)
async def test_resource_and_prompt_input_through_pinned_backend(
    backend: FastMCP, elicit: bool, expected: str
):
    proxy = create_proxy(backend, mode="auto")
    async with _front_client(proxy, "legacy", elicit) as client:
        contents = await client.read_resource("data://confirm")
        templated = await client.read_resource("data://confirm/files")
        prompt = await client.get_prompt("confirm_prompt")
    assert contents[0].text == expected
    assert templated[0].text == expected
    assert prompt.messages[0].content.text == expected


@pytest.mark.parametrize("front_mode", ["legacy", "auto"])
async def test_backend_sees_only_front_capabilities(backend: FastMCP, front_mode: str):
    async def roots(context: Any) -> list[str]:
        return []

    proxy = create_proxy(backend)
    async with Client(proxy, mode=front_mode, roots=roots) as client:
        result = await client.call_tool("declared_capabilities", {})
    assert result.data == ["roots"]


async def test_explicit_backend_handler_stays_declared(backend: FastMCP):
    """A handler passed to the proxy client answers on the proxy itself."""
    proxy = create_proxy(ProxyClient(backend, elicitation_handler=accept))
    async with Client(proxy, mode="legacy") as client:
        result = await client.call_tool("confirm_delete", {})
    assert result.data == "confirmed: accept"


@pytest.mark.parametrize(
    ("elicit", "expected"), [(True, "confirmed: accept"), (False, FALLBACK)]
)
async def test_stateful_proxy_mirrors_initialize_capabilities(
    backend: FastMCP, elicit: bool, expected: str
):
    """The stateful backend session opens during the front handshake and is kept."""
    stateful = StatefulProxyClient(transport=FastMCPTransport(backend))
    proxy = FastMCPProxy(client_factory=stateful.new_stateful)
    try:
        async with _front_client(proxy, "legacy", elicit) as client:
            first = await client.call_tool("confirm_delete", {})
            second = await client.call_tool("confirm_delete", {})
    finally:
        await stateful.clear()
    assert first.data == second.data == expected


async def test_shared_client_declares_each_callers_capabilities(backend: FastMCP):
    """A client shared across front connections follows the request that connects it."""
    shared = ProxyClient(backend).new()
    proxy = FastMCPProxy(client_factory=lambda: shared)
    async with _front_client(proxy, "legacy", elicit=True) as eliciting:
        async with _front_client(proxy, "legacy", elicit=False) as plain:
            fallback = await plain.call_tool("confirm_delete", {})
        confirmed = await eliciting.call_tool("confirm_delete", {})
    assert fallback.data == FALLBACK
    assert confirmed.data == "confirmed: accept"
