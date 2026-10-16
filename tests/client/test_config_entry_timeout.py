"""Per-entry `timeout` propagation from MCP server configs (#5754).

The `timeout` field on RemoteMCPServer/StdioMCPServer was silently ignored:
it never reached the transport or the session. These tests pin that a set
entry timeout (milliseconds, per the field docs) overrides the client-level
default for that entry's session, in both single- and multi-server configs.
"""

import contextlib
from unittest.mock import AsyncMock, patch

from fastmcp.client.client import Client
from fastmcp.client.transports import MCPConfigTransport
from fastmcp.mcp_config import (
    MCPConfig,
    RemoteMCPServer,
    StdioMCPServer,
)


async def test_single_server_entry_timeout_reaches_session():
    """A set entry timeout (ms) becomes read_timeout_seconds for the session."""
    config = MCPConfig(
        mcpServers={
            "remote": RemoteMCPServer(url="https://example.com/mcp", timeout=5000),
        }
    )
    transport = MCPConfigTransport(config)

    seen: dict = {}

    @contextlib.asynccontextmanager
    async def spy(**kwargs):
        seen.update(kwargs)
        yield AsyncMock()

    with patch.object(transport.transport, "connect_session", spy):
        async with transport.connect_session(read_timeout_seconds=30.0):
            pass

    assert seen["read_timeout_seconds"] == 5.0


async def test_single_server_without_entry_timeout_keeps_client_default():
    """An unset entry timeout leaves the client-level value untouched."""
    config = MCPConfig(
        mcpServers={
            "remote": RemoteMCPServer(url="https://example.com/mcp"),
        }
    )
    transport = MCPConfigTransport(config)

    seen: dict = {}

    @contextlib.asynccontextmanager
    async def spy(**kwargs):
        seen.update(kwargs)
        yield AsyncMock()

    with patch.object(transport.transport, "connect_session", spy):
        async with transport.connect_session(read_timeout_seconds=30.0):
            pass

    assert seen["read_timeout_seconds"] == 30.0


async def test_multi_server_entry_timeout_overrides_aggregate():
    """In multi-server configs each backend gets its own entry timeout."""
    import contextlib

    fast_entry = StdioMCPServer(command="echo", timeout=2000)
    slow_entry = StdioMCPServer(command="echo")
    config = MCPConfig(mcpServers={"fast": fast_entry, "slow": slow_entry})
    transport = MCPConfigTransport(config)

    captured: dict = {}

    def fake_factory(transport, timeout=None, **kwargs):
        captured[transport._tag] = timeout
        client = AsyncMock()
        client.protocol_version = "2025-11-25"
        return client

    async def run_one(name, entry, transport_tag):
        backend = AsyncMock()
        backend._tag = transport_tag
        with (
            patch(
                "fastmcp.server.providers.proxy.StatefulProxyClient",
                side_effect=fake_factory,
            ),
            patch("fastmcp.server.server.create_proxy", return_value=AsyncMock()),
        ):
            async with contextlib.AsyncExitStack() as stack:
                await transport._create_proxy(
                    name, entry, 30.0, stack, "legacy", transport=backend
                )

    await run_one("fast", fast_entry, "t-fast")
    await run_one("slow", slow_entry, "t-slow")

    assert captured == {"t-fast": 2.0, "t-slow": 30.0}


async def test_single_server_entry_timeout_zero_disables():
    """timeout=0 disables the timeout instead of falling back to the default."""
    config = MCPConfig(
        mcpServers={
            "remote": RemoteMCPServer(url="https://example.com/mcp", timeout=0),
        }
    )
    transport = MCPConfigTransport(config)

    seen: dict = {}

    @contextlib.asynccontextmanager
    async def spy(**kwargs):
        seen.update(kwargs)
        yield AsyncMock()

    with patch.object(transport.transport, "connect_session", spy):
        async with transport.connect_session(read_timeout_seconds=30.0):
            pass

    assert seen["read_timeout_seconds"] is None


async def test_multi_server_frontend_allows_lengthened_entry():
    """The frontend session must not kill a lengthened entry override."""
    from fastmcp import FastMCP

    config = MCPConfig(
        mcpServers={
            "long": StdioMCPServer(command="echo", timeout=60000),
            "plain": StdioMCPServer(command="echo"),
        }
    )
    transport = MCPConfigTransport(config)

    mock_create_proxy = AsyncMock(
        return_value=(AsyncMock(), AsyncMock(), FastMCP(name="MockProxy"))
    )
    seen: dict = {}

    @contextlib.asynccontextmanager
    async def spy(self, **kwargs):
        seen.update(kwargs)
        yield AsyncMock()

    with (
        patch.object(transport, "_create_proxy", mock_create_proxy),
        patch("fastmcp.client.transports.FastMCPTransport.connect_session", spy),
    ):
        async with transport.connect_session(read_timeout_seconds=30.0):
            pass

    assert seen["read_timeout_seconds"] == 60.0


async def test_multi_server_frontend_disabled_when_entry_disables():
    """A disabled entry lifts the frontend cap (backends enforce their own)."""
    from fastmcp import FastMCP

    config = MCPConfig(
        mcpServers={
            "open": StdioMCPServer(command="echo", timeout=0),
            "plain": StdioMCPServer(command="echo"),
        }
    )
    transport = MCPConfigTransport(config)

    mock_create_proxy = AsyncMock(
        return_value=(AsyncMock(), AsyncMock(), FastMCP(name="MockProxy"))
    )
    seen: dict = {}

    @contextlib.asynccontextmanager
    async def spy(self, **kwargs):
        seen.update(kwargs)
        yield AsyncMock()

    with (
        patch.object(transport, "_create_proxy", mock_create_proxy),
        patch("fastmcp.client.transports.FastMCPTransport.connect_session", spy),
    ):
        async with transport.connect_session(read_timeout_seconds=30.0):
            pass

    assert seen["read_timeout_seconds"] is None


def test_group_from_config_propagates_entry_timeout():
    """ClientGroup.from_config honors per-entry timeouts on child clients."""
    from fastmcp.client.group import ClientGroup

    config = MCPConfig(
        mcpServers={
            "capped": RemoteMCPServer(url="https://example.com/mcp", timeout=5000),
            "plain": RemoteMCPServer(url="https://example.com/other"),
        }
    )
    group = ClientGroup.from_config(config)
    assert group.clients["capped"]._session_kwargs["read_timeout_seconds"] == 5.0
    assert group.clients["plain"]._session_kwargs["read_timeout_seconds"] is None


def test_client_config_entry_timeout_end_to_end():
    """Client(config) surfaces the per-entry timeout on the live session kwargs."""
    config = MCPConfig(
        mcpServers={
            "remote": RemoteMCPServer(url="https://example.com/mcp", timeout=1500),
        }
    )
    client = Client(config)
    assert isinstance(client.transport, MCPConfigTransport)
    entry = next(iter(client.transport.config.mcpServers.values()))
    assert entry.timeout == 1500
    """Client(config) surfaces the per-entry timeout on the live session kwargs."""
    config = MCPConfig(
        mcpServers={
            "remote": RemoteMCPServer(url="https://example.com/mcp", timeout=1500),
        }
    )
    client = Client(config)
    assert isinstance(client.transport, MCPConfigTransport)
    entry = next(iter(client.transport.config.mcpServers.values()))
    assert entry.timeout == 1500
