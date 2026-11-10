"""Request `_meta` ownership at the proxy's backend connection boundary.

Protocol version, client identity, and client capabilities describe one
negotiated MCP connection. The proxy must never copy them from its frontend
connection onto its backend connection: a modern backend session stamps its
own values, and a handshake-era backend must not receive them at all.
Progress, tracing, task, and application metadata pass through untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import mcp_types
import pytest
from mcp.client.extension import ClientExtension
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp import Client, Context, FastMCP
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient

FRONT_EXTENSION_ID = "example.com/frontend"
FRONT_INFO = mcp_types.Implementation(name="frontend-client", version="1.0")
BACKEND_INFO = mcp_types.Implementation(name="proxy-backend", version="1.0")
RESERVED_META_KEYS = {
    mcp_types.PROTOCOL_VERSION_META_KEY,
    mcp_types.CLIENT_INFO_META_KEY,
    mcp_types.CLIENT_CAPABILITIES_META_KEY,
}


@dataclass
class _RecordedRequest:
    protocol_version: str
    meta: dict[str, Any]


class _FrontendExtension(ClientExtension):
    identifier = FRONT_EXTENSION_ID

    def settings(self) -> dict[str, Any]:
        return {"frontend": True}


def _recording_backend(seen: dict[str, _RecordedRequest]) -> FastMCP:
    backend = FastMCP("metadata-backend")

    def record(operation: str, ctx: Context) -> None:
        request_context = ctx.request_context
        assert request_context is not None
        seen[operation] = _RecordedRequest(
            protocol_version=request_context.protocol_version,
            meta=dict(request_context.meta or {}),
        )

    @backend.tool
    def inspect_tool(ctx: Context) -> str:
        record("tool", ctx)
        return "ok"

    @backend.resource("data://metadata")
    def inspect_resource(ctx: Context) -> str:
        record("resource", ctx)
        return "ok"

    @backend.resource("data://items/{item_id}")
    def inspect_template(item_id: str, ctx: Context) -> str:
        record("template", ctx)
        return "ok"

    @backend.prompt
    def inspect_prompt(ctx: Context) -> str:
        record("prompt", ctx)
        return "ok"

    return backend


def _proxy(
    backend: FastMCP, *, backend_mode: str, client_class: type[Client]
) -> FastMCPProxy:
    return FastMCPProxy(
        client_factory=lambda: client_class(
            backend,
            mode=backend_mode,
            client_info=BACKEND_INFO,
        )
    )


def _assert_backend_connection_meta(record: _RecordedRequest, modern: bool) -> None:
    """The backend request carries the backend connection's own envelope.

    On a handshake-era backend the reserved keys are absent. On a modern
    backend they hold the backend session's negotiated version and the proxy
    client's identity and capabilities — never the frontend client's.
    """
    meta = record.meta
    if not modern:
        assert RESERVED_META_KEYS.isdisjoint(meta)
        return

    assert meta[mcp_types.PROTOCOL_VERSION_META_KEY] == record.protocol_version
    assert meta[mcp_types.CLIENT_INFO_META_KEY] == BACKEND_INFO.model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    capabilities = meta[mcp_types.CLIENT_CAPABILITIES_META_KEY]
    assert FRONT_EXTENSION_ID not in capabilities.get("extensions", {})


# Every allowed ClientFactoryT shape must be hop-safe, not just ProxyClient:
# a plain Client backend runs the SDK's stock ClientSession rather than the
# proxy's session class, so it exercises the copy-site sanitization alone.
@pytest.mark.parametrize("client_class", [ProxyClient, Client])
@pytest.mark.parametrize(
    ("front_mode", "backend_mode", "backend_is_modern"),
    [
        ("auto", "auto", True),
        ("auto", "legacy", False),
        ("legacy", "auto", True),
        ("legacy", "legacy", False),
    ],
)
async def test_forwarded_tool_meta_stays_hop_safe(
    front_mode: str,
    backend_mode: str,
    backend_is_modern: bool,
    client_class: type[Client],
):
    seen: dict[str, _RecordedRequest] = {}
    proxy = _proxy(
        _recording_backend(seen), backend_mode=backend_mode, client_class=client_class
    )

    async with Client(
        proxy,
        mode=front_mode,
        client_info=FRONT_INFO,
        extensions=[_FrontendExtension()],
    ) as client:
        await client.call_tool(
            "inspect_tool",
            meta={
                "progressToken": "front-progress",
                "example.com/vendor": {"request": "kept"},
            },
        )

    record = seen["tool"]
    assert (record.protocol_version in MODERN_PROTOCOL_VERSIONS) is backend_is_modern
    assert isinstance(record.meta["progressToken"], str | int)
    assert record.meta["example.com/vendor"] == {"request": "kept"}
    _assert_backend_connection_meta(record, backend_is_modern)


@pytest.mark.parametrize("client_class", [ProxyClient, Client])
@pytest.mark.parametrize(
    ("backend_mode", "backend_is_modern"),
    [("auto", True), ("legacy", False)],
)
@pytest.mark.parametrize("operation", ["resource", "template", "prompt"])
async def test_non_tool_requests_forward_hop_safe_metadata(
    operation: str,
    backend_mode: str,
    backend_is_modern: bool,
    client_class: type[Client],
):
    seen: dict[str, _RecordedRequest] = {}
    proxy = _proxy(
        _recording_backend(seen), backend_mode=backend_mode, client_class=client_class
    )
    meta = {"example.com/vendor": {"operation": operation}}

    async with Client(
        proxy,
        mode="auto",
        client_info=FRONT_INFO,
        extensions=[_FrontendExtension()],
    ) as client:
        if operation == "resource":
            await client.read_resource("data://metadata", meta=meta)
        elif operation == "template":
            await client.read_resource("data://items/42", meta=meta)
        else:
            await client.get_prompt("inspect_prompt", meta=meta)

    record = seen[operation]
    assert (record.protocol_version in MODERN_PROTOCOL_VERSIONS) is backend_is_modern
    assert record.meta["example.com/vendor"] == {"operation": operation}
    _assert_backend_connection_meta(record, backend_is_modern)
