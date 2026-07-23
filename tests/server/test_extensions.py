"""Tests for the FastMCP-native server extension API (SEP-2133).

A synthetic extension exercises every contribution kind: capability
advertisement, additive request methods (with protocol-version gating),
tools/call interception (observe and short-circuit), a lifespan hook (order and
mounted-server behaviour), and the per-request capability sniff. Registration
guards (duplicate identifier, spec-method rejection, invalid identifier) and a
zero-behaviour-change baseline round it out.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal, cast

import mcp_types
import pytest
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import CLIENT_CAPABILITIES_META_KEY, METHOD_NOT_FOUND, RequestParams

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.extensions import (
    MethodBinding,
    ServerExtension,
    read_client_extension_settings,
)
from fastmcp.tools.base import ToolResult

EXT_ID = "com.example/synthetic"


class PingParams(RequestParams):
    echo: str | None = None


class PingRequest(mcp_types.Request):
    method: Literal["synthetic/ping"] = "synthetic/ping"
    params: PingParams


class PingResult(mcp_types.Result):
    pong: bool
    echo: str | None = None


def _text(result: mcp_types.CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, mcp_types.TextContent)
    return block.text


# ---------------------------------------------------------------------------
# Capability advertisement
# ---------------------------------------------------------------------------


async def test_capability_advertised_to_modern_client():
    """A registered extension's settings appear under capabilities.extensions.

    Uses ``mode='auto'`` so the client negotiates the modern era via
    ``server/discover`` (which reads ``get_capabilities`` directly); the SDK's
    version sieve strips ``capabilities.extensions`` only on legacy eras.
    """

    class Ext(ServerExtension):
        identifier = EXT_ID

        def settings(self) -> dict[str, Any]:
            return {"version": "1"}

    mcp = FastMCP("t")
    mcp.add_extension(Ext())

    async with Client(mcp, mode="auto") as client:
        extensions = client.server_capabilities.extensions or {}
        assert extensions.get(EXT_ID) == {"version": "1"}


async def test_capability_absent_without_registration():
    """A server with no extensions advertises none of its own."""
    mcp = FastMCP("t")
    async with Client(mcp, mode="auto") as client:
        extensions = client.server_capabilities.extensions or {}
        assert EXT_ID not in extensions


async def test_empty_settings_still_advertise():
    """The default empty-settings extension is advertised with an empty dict."""

    class Ext(ServerExtension):
        identifier = EXT_ID

    mcp = FastMCP("t")
    mcp.add_extension(Ext())
    async with Client(mcp, mode="auto") as client:
        extensions = client.server_capabilities.extensions or {}
        assert extensions.get(EXT_ID) == {}


# ---------------------------------------------------------------------------
# Additive request methods
# ---------------------------------------------------------------------------


class _PingExtension(ServerExtension):
    identifier = EXT_ID

    def methods(self) -> list[MethodBinding]:
        async def handler(
            ctx: ServerRequestContext[Any, Any], params: PingParams
        ) -> PingResult:
            return PingResult(pong=True, echo=params.echo)

        return [
            MethodBinding(
                method="synthetic/ping",
                params_type=PingParams,
                handler=handler,
            )
        ]


async def test_custom_method_callable_end_to_end():
    mcp = FastMCP("t")
    mcp.add_extension(_PingExtension())

    async with Client(mcp, mode="auto") as client:
        result = await client.session.send_request(
            request=PingRequest(params=PingParams(echo="hi")),
            result_type=PingResult,
        )
        assert result.pong is True
        assert result.echo == "hi"


async def test_method_handler_reaches_server_registry():
    """A method handler can reach the FastMCP component registry via the extension."""

    class Ext(ServerExtension):
        identifier = EXT_ID

        def methods(self) -> list[MethodBinding]:
            async def handler(
                ctx: ServerRequestContext[Any, Any], params: PingParams
            ) -> PingResult:
                tools = await self.server.list_tools()
                return PingResult(pong=len(tools) == 1)

            return [
                MethodBinding(
                    method="synthetic/ping",
                    params_type=PingParams,
                    handler=handler,
                )
            ]

    mcp = FastMCP("t")

    @mcp.tool
    def only_tool() -> str:
        return "x"

    mcp.add_extension(Ext())
    async with Client(mcp, mode="auto") as client:
        result = await client.session.send_request(
            request=PingRequest(params=PingParams()),
            result_type=PingResult,
        )
        assert result.pong is True


async def test_method_protocol_version_gating():
    """A version-gated method is rejected as METHOD_NOT_FOUND off its versions."""

    class Ext(ServerExtension):
        identifier = EXT_ID

        def methods(self) -> list[MethodBinding]:
            async def handler(
                ctx: ServerRequestContext[Any, Any], params: PingParams
            ) -> PingResult:
                return PingResult(pong=True)

            return [
                MethodBinding(
                    method="synthetic/ping",
                    params_type=PingParams,
                    handler=handler,
                    protocol_versions=frozenset({"2026-07-28"}),
                )
            ]

    mcp = FastMCP("t")
    mcp.add_extension(Ext())

    async with Client(mcp, mode="legacy") as client:
        with pytest.raises(MCPError) as exc_info:
            await client.session.send_request(
                request=PingRequest(params=PingParams()),
                result_type=PingResult,
            )
        assert exc_info.value.error.code == METHOD_NOT_FOUND


# ---------------------------------------------------------------------------
# tools/call interception
# ---------------------------------------------------------------------------


async def test_interceptor_observes_tool_call():
    """A pass-through interceptor sees the call and the tool still runs."""
    seen: list[str] = []

    class Ext(ServerExtension):
        identifier = EXT_ID

        async def intercept_tool_call(self, params, context, call_next):
            seen.append(params.name)
            return await call_next()

    mcp = FastMCP("t")

    @mcp.tool
    def greet() -> str:
        return "hello"

    mcp.add_extension(Ext())
    async with Client(mcp, mode="auto") as client:
        result = await client.call_tool("greet")
        assert _text(result) == "hello"
    assert seen == ["greet"]


async def test_interceptor_short_circuits():
    """An interceptor can return its own result without running the tool body."""
    ran = []

    class Ext(ServerExtension):
        identifier = EXT_ID

        async def intercept_tool_call(self, params, context, call_next):
            return ToolResult(
                content=[mcp_types.TextContent(type="text", text="intercepted")]
            )

    mcp = FastMCP("t")

    @mcp.tool
    def greet():
        ran.append(True)
        return "hello"

    mcp.add_extension(Ext())
    async with Client(mcp, mode="auto") as client:
        result = await client.call_tool("greet")
        assert _text(result) == "intercepted"
    assert ran == []


async def test_interceptor_reaches_tool_metadata():
    """An interceptor can resolve the tool being called through the context."""
    captured: dict[str, Any] = {}

    class Ext(ServerExtension):
        identifier = EXT_ID

        async def intercept_tool_call(self, params, context, call_next):
            tool = await context.fastmcp.get_tool(params.name)
            captured["title"] = tool.title
            return await call_next()

    mcp = FastMCP("t")

    @mcp.tool(title="A Greeting")
    def greet() -> str:
        return "hello"

    mcp.add_extension(Ext())
    async with Client(mcp, mode="auto") as client:
        await client.call_tool("greet")
    assert captured["title"] == "A Greeting"


async def test_interceptors_nest_first_registered_outermost():
    """Multiple interceptors nest with the first-registered extension outermost."""
    order: list[str] = []

    def make_ext(identifier: str, label: str) -> ServerExtension:
        class Ext(ServerExtension):
            async def intercept_tool_call(self, params, context, call_next):
                order.append(f"{label}-before")
                result = await call_next()
                order.append(f"{label}-after")
                return result

        ext = Ext()
        ext.identifier = identifier
        return ext

    mcp = FastMCP("t")

    @mcp.tool
    def greet() -> str:
        return "hello"

    mcp.add_extension(make_ext("com.example/outer", "outer"))
    mcp.add_extension(make_ext("com.example/inner", "inner"))
    async with Client(mcp, mode="auto") as client:
        await client.call_tool("greet")

    assert order == ["outer-before", "inner-before", "inner-after", "outer-after"]


async def test_no_extensions_leaves_tool_call_unchanged():
    """With no extensions registered, tools/call behaves exactly as before."""
    mcp = FastMCP("t")

    @mcp.tool
    def greet() -> str:
        return "hello"

    assert mcp._extensions == {}
    async with Client(mcp, mode="auto") as client:
        result = await client.call_tool("greet")
        assert _text(result) == "hello"


# ---------------------------------------------------------------------------
# Lifespan hook
# ---------------------------------------------------------------------------


def _recording_extension(identifier: str, log: list[str]) -> ServerExtension:
    class Ext(ServerExtension):
        @asynccontextmanager
        async def lifespan(self):
            log.append(f"{identifier}:enter")
            try:
                yield
            finally:
                log.append(f"{identifier}:exit")

    ext = Ext()
    ext.identifier = identifier
    return ext


async def test_lifespan_entered_and_exited():
    log: list[str] = []
    mcp = FastMCP("t")
    mcp.add_extension(_recording_extension(EXT_ID, log))

    async with Client(mcp, mode="auto"):
        assert log == [f"{EXT_ID}:enter"]
    assert log == [f"{EXT_ID}:enter", f"{EXT_ID}:exit"]


async def test_lifespans_enter_in_order_exit_in_reverse():
    log: list[str] = []
    mcp = FastMCP("t")
    mcp.add_extension(_recording_extension("com.example/a", log))
    mcp.add_extension(_recording_extension("com.example/b", log))

    async with Client(mcp, mode="auto"):
        pass

    assert log == [
        "com.example/a:enter",
        "com.example/b:enter",
        "com.example/b:exit",
        "com.example/a:exit",
    ]


async def test_standalone_server_enters_extension_lifespan():
    log: list[str] = []
    child = FastMCP("child")
    child.add_extension(_recording_extension(EXT_ID, log))

    async with Client(child, mode="auto"):
        assert log == [f"{EXT_ID}:enter"]
    assert log == [f"{EXT_ID}:enter", f"{EXT_ID}:exit"]


async def test_mounted_child_defers_extension_lifespan_to_root():
    """A mounted child's extension lifespan is not entered below a root.

    Mirrors the shared Docket: extension lifespans that may start shared
    infrastructure are owned by the tree root, so a mounted child defers.
    """
    log: list[str] = []
    child = FastMCP("child")
    child.add_extension(_recording_extension(EXT_ID, log))

    root = FastMCP("root")
    root.mount(child)

    async with Client(root, mode="auto"):
        pass

    assert log == []


# ---------------------------------------------------------------------------
# Request-time capability sniff
# ---------------------------------------------------------------------------


def _ctx_with_meta(meta: dict[str, Any] | None) -> ServerRequestContext[Any, Any]:
    params: dict[str, Any] = {}
    if meta is not None:
        params["_meta"] = meta
    return ServerRequestContext(
        session=cast(Any, object()),
        lifespan_context={},
        protocol_version="2026-07-28",
        method="synthetic/ping",
        params=params,
    )


def test_capability_sniff_reads_declared_settings():
    meta = {
        CLIENT_CAPABILITIES_META_KEY: {
            "extensions": {EXT_ID: {"limit": 5}},
        }
    }
    assert read_client_extension_settings(_ctx_with_meta(meta), EXT_ID) == {"limit": 5}


def test_capability_sniff_empty_settings_is_opt_in():
    """An empty settings dict is a valid opt-in, distinct from absence (None)."""
    meta = {CLIENT_CAPABILITIES_META_KEY: {"extensions": {EXT_ID: {}}}}
    assert read_client_extension_settings(_ctx_with_meta(meta), EXT_ID) == {}


@pytest.mark.parametrize(
    "meta",
    [
        None,
        {},
        {CLIENT_CAPABILITIES_META_KEY: {}},
        {CLIENT_CAPABILITIES_META_KEY: {"extensions": {"other/ext": {}}}},
    ],
)
def test_capability_sniff_returns_none_when_not_opted_in(meta):
    assert read_client_extension_settings(_ctx_with_meta(meta), EXT_ID) is None


def test_client_settings_convenience_uses_own_identifier():
    class Ext(ServerExtension):
        identifier = EXT_ID

    meta = {CLIENT_CAPABILITIES_META_KEY: {"extensions": {EXT_ID: {"a": 1}}}}
    assert Ext().client_settings(_ctx_with_meta(meta)) == {"a": 1}


# ---------------------------------------------------------------------------
# Registration guards
# ---------------------------------------------------------------------------


async def test_duplicate_identifier_rejected():
    class Ext(ServerExtension):
        identifier = EXT_ID

    mcp = FastMCP("t")
    mcp.add_extension(Ext())
    with pytest.raises(ValueError, match="already registered"):
        mcp.add_extension(Ext())


def test_spec_method_name_rejected():
    async def handler(ctx: Any, params: Any) -> None:
        return None

    with pytest.raises(ValueError, match="spec method"):
        MethodBinding(
            method="tools/call",
            params_type=PingParams,
            handler=handler,
        )


def test_empty_protocol_versions_rejected():
    async def handler(ctx: Any, params: Any) -> None:
        return None

    with pytest.raises(ValueError, match="protocol_versions"):
        MethodBinding(
            method="synthetic/ping",
            params_type=PingParams,
            handler=handler,
            protocol_versions=frozenset(),
        )


def test_invalid_identifier_rejected_at_class_definition():
    with pytest.raises(TypeError, match="reverse-DNS"):

        class Ext(ServerExtension):
            identifier = "no-prefix"


async def test_per_instance_invalid_identifier_rejected_at_registration():
    class Ext(ServerExtension):
        pass

    ext = Ext()
    ext.identifier = "no-prefix"
    mcp = FastMCP("t")
    with pytest.raises(TypeError, match="reverse-DNS"):
        mcp.add_extension(ext)


def test_bound_server_accessible_after_registration():
    class Ext(ServerExtension):
        identifier = EXT_ID

    ext = Ext()
    mcp = FastMCP("t")
    mcp.add_extension(ext)
    assert ext.server is mcp


def test_unbound_server_access_raises():
    class Ext(ServerExtension):
        identifier = EXT_ID

    with pytest.raises(RuntimeError, match="not bound"):
        _ = Ext().server
