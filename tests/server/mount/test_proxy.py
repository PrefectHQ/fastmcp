"""Tests for proxy server mounting."""

import json
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.server.providers import FastMCPProvider
from fastmcp.server.providers.proxy import FastMCPProxy
from fastmcp.server.providers.wrapped_provider import _WrappedProvider
from fastmcp.server.transforms import Namespace


class TestProxyServer:
    """Test mounting a proxy server."""

    async def test_mount_proxy_server(self):
        """Test mounting a proxy server."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        @original_server.tool
        def get_data(query: str) -> str:
            return f"Data for {query}"

        # Create proxy server
        proxy_server = FastMCP.as_proxy(FastMCPTransport(original_server))

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount(proxy_server, "proxy")

        # Tool should be accessible through main app
        tools = await main_app.list_tools()
        assert any(t.name == "proxy_get_data" for t in tools)

        # Call the tool
        result = await main_app.call_tool("proxy_get_data", {"query": "test"})
        assert result.structured_content == {"result": "Data for test"}

    async def test_dynamically_adding_to_proxied_server(self):
        """Test that changes to the original server are reflected in the mounted proxy."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        # Create proxy server
        proxy_server = FastMCP.as_proxy(FastMCPTransport(original_server))

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount(proxy_server, "proxy")

        # Add a tool to the original server
        @original_server.tool
        def dynamic_data() -> str:
            return "Dynamic data"

        # Tool should be accessible through main app via proxy
        tools = await main_app.list_tools()
        assert any(t.name == "proxy_dynamic_data" for t in tools)

        # Call the tool
        result = await main_app.call_tool("proxy_dynamic_data", {})
        assert result.structured_content == {"result": "Dynamic data"}

    async def test_proxy_server_with_resources(self):
        """Test mounting a proxy server with resources."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        @original_server.resource(uri="config://settings")
        def get_config() -> str:
            return json.dumps({"api_key": "12345"})

        # Create proxy server
        proxy_server = FastMCP.as_proxy(FastMCPTransport(original_server))

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount(proxy_server, "proxy")

        # Resource should be accessible through main app
        result = await main_app.read_resource("config://proxy/settings")
        assert len(result.contents) == 1
        config = json.loads(result.contents[0].content)
        assert config["api_key"] == "12345"

    async def test_proxy_server_with_prompts(self):
        """Test mounting a proxy server with prompts."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        @original_server.prompt
        def welcome(name: str) -> str:
            return f"Welcome, {name}!"

        # Create proxy server
        proxy_server = FastMCP.as_proxy(FastMCPTransport(original_server))

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount(proxy_server, "proxy")

        # Prompt should be accessible through main app
        result = await main_app.render_prompt("proxy_welcome", {"name": "World"})
        assert result.messages is not None
        # The message should contain our welcome text


class TestAsProxyKwarg:
    """Test the as_proxy kwarg."""

    async def test_as_proxy_defaults_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        @sub.tool
        def sub_tool() -> str:
            return "test"

        mcp.mount(sub, "sub")
        # Index 1 because LocalProvider is at index 0
        provider = mcp.providers[1]
        # Provider is wrapped with Namespace transform
        assert isinstance(provider, _WrappedProvider)
        assert len(provider._transforms) == 1
        assert isinstance(provider._transforms[0], Namespace)
        # Inner provider is FastMCPProvider
        assert isinstance(provider._inner, FastMCPProvider)
        assert provider._inner.server is sub
        # Verify namespace is applied
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {"sub_sub_tool"}

    async def test_as_proxy_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        @sub.tool
        def sub_tool() -> str:
            return "test"

        mcp.mount(sub, "sub", as_proxy=False)

        # Index 1 because LocalProvider is at index 0
        provider = mcp.providers[1]
        # Provider is wrapped with Namespace transform
        assert isinstance(provider, _WrappedProvider)
        assert len(provider._transforms) == 1
        assert isinstance(provider._transforms[0], Namespace)
        # Inner provider is FastMCPProvider
        assert isinstance(provider._inner, FastMCPProvider)
        assert provider._inner.server is sub
        # Verify namespace is applied
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {"sub_sub_tool"}

    async def test_as_proxy_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        @sub.tool
        def sub_tool() -> str:
            return "test"

        mcp.mount(sub, "sub", as_proxy=True)

        # Index 1 because LocalProvider is at index 0
        provider = mcp.providers[1]
        # Provider is wrapped with Namespace transform
        assert isinstance(provider, _WrappedProvider)
        assert len(provider._transforms) == 1
        assert isinstance(provider._transforms[0], Namespace)
        # Inner provider is FastMCPProvider wrapping a proxy
        assert isinstance(provider._inner, FastMCPProvider)
        assert provider._inner.server is not sub
        assert isinstance(provider._inner.server, FastMCPProxy)
        # Verify namespace is applied
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {"sub_sub_tool"}

    async def test_lifespan_server_mounted_directly(self):
        """Test that servers with lifespan are mounted directly (not auto-proxied).

        Since FastMCPProvider now handles lifespan via the provider lifespan interface,
        there's no need to auto-convert to a proxy. The server is mounted directly.
        """

        @asynccontextmanager
        async def server_lifespan(mcp: FastMCP):
            yield

        mcp = FastMCP("Main")
        sub = FastMCP("Sub", lifespan=server_lifespan)

        @sub.tool
        def sub_tool() -> str:
            return "test"

        mcp.mount(sub, "sub")

        # Server should be mounted directly without auto-proxying
        # Index 1 because LocalProvider is at index 0
        provider = mcp.providers[1]
        # Provider is wrapped with Namespace transform
        assert isinstance(provider, _WrappedProvider)
        assert len(provider._transforms) == 1
        assert isinstance(provider._transforms[0], Namespace)
        # Inner provider is FastMCPProvider
        assert isinstance(provider._inner, FastMCPProvider)
        assert provider._inner.server is sub
        # Verify namespace is applied
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == {"sub_sub_tool"}

    async def test_as_proxy_ignored_for_proxy_mounts_default(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub")

        # Index 1 because LocalProvider is at index 0
        provider = mcp.providers[1]
        # Provider is wrapped with Namespace transform
        assert isinstance(provider, _WrappedProvider)
        assert len(provider._transforms) == 1
        assert isinstance(provider._transforms[0], Namespace)
        # Inner provider is FastMCPProvider
        assert isinstance(provider._inner, FastMCPProvider)
        assert provider._inner.server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub", as_proxy=False)

        # Index 1 because LocalProvider is at index 0
        provider = mcp.providers[1]
        # Provider is wrapped with Namespace transform
        assert isinstance(provider, _WrappedProvider)
        assert len(provider._transforms) == 1
        assert isinstance(provider._transforms[0], Namespace)
        # Inner provider is FastMCPProvider
        assert isinstance(provider._inner, FastMCPProvider)
        assert provider._inner.server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub", as_proxy=True)

        # Index 1 because LocalProvider is at index 0
        provider = mcp.providers[1]
        # Provider is wrapped with Namespace transform
        assert isinstance(provider, _WrappedProvider)
        assert len(provider._transforms) == 1
        assert isinstance(provider._transforms[0], Namespace)
        # Inner provider is FastMCPProvider
        assert isinstance(provider._inner, FastMCPProvider)
        assert provider._inner.server is sub_proxy

    async def test_as_proxy_mounts_still_have_live_link(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub", as_proxy=True)

        assert len(await mcp.list_tools()) == 0

        @sub.tool
        def hello():
            return "hi"

        assert len(await mcp.list_tools()) == 1

    async def test_sub_lifespan_is_executed(self):
        lifespan_check = []

        @asynccontextmanager
        async def lifespan(mcp: FastMCP):
            lifespan_check.append("start")
            yield

        mcp = FastMCP("Main")
        sub = FastMCP("Sub", lifespan=lifespan)

        @sub.tool
        def hello():
            return "hi"

        mcp.mount(sub, as_proxy=True)

        assert lifespan_check == []

        async with Client(mcp) as client:
            await client.call_tool("hello", {})

        # Lifespan is executed at least once (may be multiple times for proxy connections)
        assert len(lifespan_check) >= 1
        assert all(x == "start" for x in lifespan_check)
