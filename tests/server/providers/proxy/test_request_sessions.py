import asyncio
from typing import Any

import mcp_types
import pytest
from mcp.shared.exceptions import MCPError

from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider


class InitializeCounter(Middleware):
    def __init__(self) -> None:
        self.count = 0

    async def on_initialize(
        self,
        context: MiddlewareContext[mcp_types.InitializeRequest],
        call_next: CallNext[
            mcp_types.InitializeRequest, mcp_types.InitializeResult | None
        ],
    ) -> mcp_types.InitializeResult | None:
        self.count += 1
        return await call_next(context)


async def raw_call_tool(
    client: Client, name: str, arguments: dict[str, Any] | None = None
) -> mcp_types.CallToolResult:
    request = mcp_types.CallToolRequest(
        params=mcp_types.CallToolRequestParams(
            name=name,
            arguments=arguments or {},
        )
    )
    return await client.session.send_request(request, mcp_types.CallToolResult)


def echo_backend(counter: InitializeCounter) -> FastMCP:
    backend = FastMCP("backend", middleware=[counter])

    @backend.tool
    def echo(value: str) -> str:
        return value

    return backend


async def test_raw_tool_call_uses_one_backend_handshake():
    counter = InitializeCounter()
    backend = echo_backend(counter)
    base_client = ProxyClient(backend)
    factory_calls = 0

    def client_factory() -> ProxyClient:
        nonlocal factory_calls
        factory_calls += 1
        return base_client

    provider = ProxyProvider(client_factory, cache_ttl=0)
    proxy = FastMCP("proxy", providers=[provider])

    async with Client(proxy, mode="legacy") as client:
        result = await raw_call_tool(client, "echo", {"value": "hello"})

    assert result.structured_content == {"result": "hello"}
    assert counter.count == 1
    assert factory_calls == 1


async def test_operation_scope_preserves_per_operation_lifecycle():
    counter = InitializeCounter()
    backend = echo_backend(counter)
    provider = ProxyProvider(
        lambda: ProxyClient(backend),
        cache_ttl=0,
        session_scope="operation",
    )
    proxy = FastMCP("proxy", providers=[provider])

    async with Client(proxy, mode="legacy") as client:
        result = await raw_call_tool(client, "echo", {"value": "legacy"})

    assert result.structured_content == {"result": "legacy"}
    assert counter.count == 2


def test_invalid_session_scope_is_rejected():
    with pytest.raises(ValueError, match="session_scope"):
        ProxyProvider(
            lambda: ProxyClient(FastMCP()),
            session_scope="invalid",  # ty: ignore[invalid-argument-type]
        )


async def test_second_request_uses_fresh_backend_session():
    counter = InitializeCounter()
    backend = echo_backend(counter)
    base_client = ProxyClient(backend)
    provider = ProxyProvider(lambda: base_client, cache_ttl=0)
    proxy = FastMCP("proxy", providers=[provider])

    async with Client(proxy, mode="legacy") as client:
        first = await raw_call_tool(client, "echo", {"value": "first"})
        assert counter.count == 1
        assert not base_client.is_connected()

        second = await raw_call_tool(client, "echo", {"value": "second"})
        assert counter.count == 2

    assert first.structured_content == {"result": "first"}
    assert second.structured_content == {"result": "second"}
    assert not base_client.is_connected()


async def test_concurrent_requests_use_distinct_backend_sessions():
    counter = InitializeCounter()
    backend = FastMCP("backend", middleware=[counter])
    both_started = asyncio.Event()
    session_ids: set[str] = set()

    @backend.tool
    async def hold(ctx: Context) -> str:
        session_ids.add(ctx.session_id)
        if len(session_ids) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return ctx.session_id

    base_client = ProxyClient(backend)
    provider = ProxyProvider(lambda: base_client, cache_ttl=0)
    proxy = FastMCP("proxy", providers=[provider])

    async with Client(proxy, mode="legacy") as client:
        first, second = await asyncio.gather(
            raw_call_tool(client, "hold"),
            raw_call_tool(client, "hold"),
        )

    assert not first.is_error
    assert not second.is_error
    assert len(session_ids) == 2
    assert counter.count == 2


async def test_local_tool_does_not_create_proxy_client():
    backend = FastMCP("backend")
    factory_calls = 0

    def client_factory() -> ProxyClient:
        nonlocal factory_calls
        factory_calls += 1
        return ProxyClient(backend)

    proxy = FastMCP(
        "mixed",
        providers=[ProxyProvider(client_factory, cache_ttl=0)],
    )

    @proxy.tool
    def local_echo(value: str) -> str:
        return value

    async with Client(proxy, mode="legacy") as client:
        result = await raw_call_tool(client, "local_echo", {"value": "local"})

    assert result.structured_content == {"result": "local"}
    assert factory_calls == 0


async def test_backend_tool_error_shape_is_preserved():
    backend = FastMCP("backend")

    @backend.tool
    def explode() -> None:
        raise ToolError("backend exploded")

    proxy = FastMCP(
        "proxy",
        providers=[ProxyProvider(lambda: ProxyClient(backend), cache_ttl=0)],
    )

    async with Client(proxy, mode="legacy") as client:
        result = await raw_call_tool(client, "explode")

    assert result.is_error
    assert len(result.content) == 1
    assert isinstance(result.content[0], mcp_types.TextContent)
    assert result.content[0].text == "backend exploded"


class CleanupFailureClient(ProxyClient):
    cleanup_failures: int

    def __init__(self, backend: FastMCP) -> None:
        super().__init__(backend)
        self.cleanup_failures = 0

    async def _disconnect(self, force: bool = False) -> None:
        was_connected = self.is_connected()
        await super()._disconnect(force=force)
        if was_connected and not self.is_connected():
            self.cleanup_failures += 1
            raise RuntimeError("backend cleanup failed")


async def test_cleanup_failure_does_not_replace_tool_response():
    counter = InitializeCounter()
    backend = echo_backend(counter)
    backend_client = CleanupFailureClient(backend)
    proxy = FastMCP(
        "proxy",
        providers=[ProxyProvider(lambda: backend_client, cache_ttl=0)],
    )

    async with Client(proxy, mode="legacy") as client:
        result = await raw_call_tool(client, "echo", {"value": "complete"})

    assert result.structured_content == {"result": "complete"}
    assert backend_client.cleanup_failures == 1


class DiesOnceClient(ProxyClient):
    should_die: bool

    def __init__(self, backend: FastMCP) -> None:
        super().__init__(backend)
        self.should_die = True

    async def list_tools(self, max_pages: int = 250) -> list[mcp_types.Tool]:
        if self.should_die:
            self.should_die = False
            session_task = self._session_state.session_task
            assert session_task is not None
            session_task.cancel()
            try:
                await session_task
            except asyncio.CancelledError:
                pass
            raise RuntimeError("backend session died")
        return await super().list_tools(max_pages=max_pages)


async def test_later_backend_use_reconnects_after_retained_session_dies():
    counter = InitializeCounter()
    backend = echo_backend(counter)
    backend_client = DiesOnceClient(backend)
    provider = ProxyProvider(lambda: backend_client, cache_ttl=0)
    proxy = FastMCP("proxy")

    @proxy.tool
    async def recover() -> int:
        try:
            await provider._list_tools()
        except MCPError:
            pass
        return len(await provider._list_tools())

    async with Client(proxy, mode="legacy") as client:
        result = await raw_call_tool(client, "recover")

    assert result.structured_content == {"result": 1}
    assert counter.count == 2
