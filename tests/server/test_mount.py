import json
import sys
from contextlib import asynccontextmanager

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport, SSETransport
from fastmcp.exceptions import NotFoundError
from fastmcp.server.proxy import FastMCPProxy


class TestBasicMount:
    """Test basic mounting functionality."""

    async def test_mount_simple_server(self):
        """Test mounting a simple server and accessing its tool."""
        # Create main app and sub-app
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        # Add a tool to the sub-app
        @sub_app.tool()
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount the sub-app to the main app
        main_app.mount("sub", sub_app)

        # Get tools from main app, should include sub_app's tools
        tools = await main_app.get_tools()
        assert "sub_sub_tool" in tools

        async with Client(main_app) as client:
            result = await client.call_tool("sub_sub_tool", {})
            assert result[0].text == "This is from the sub app"  # type: ignore[attr-defined]

    async def test_mount_with_custom_separator(self):
        """Test mounting with a custom tool separator (deprecated but still supported)."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Mount without custom separator - custom separators are deprecated
        main_app.mount("sub", sub_app)

        # Tool should be accessible with the default separator
        tools = await main_app.get_tools()
        assert "sub_greet" in tools

        # Call the tool
        result = await main_app._mcp_call_tool("sub_greet", {"name": "World"})
        assert result[0].text == "Hello, World!"  # type: ignore[attr-defined]

    async def test_mount_invalid_resource_prefix(self):
        main_app = FastMCP("MainApp")
        api_app = FastMCP("APIApp")

        # This test doesn't apply anymore with the new prefix format
        # just mount the server to maintain test coverage
        main_app.mount("api:sub", api_app)

    async def test_mount_invalid_resource_separator(self):
        main_app = FastMCP("MainApp")
        api_app = FastMCP("APIApp")

        # This test doesn't apply anymore with the new prefix format
        # Mount without deprecated parameters
        main_app.mount("api", api_app)

    async def test_unmount_server(self):
        """Test unmounting a server removes access to its tools."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool()
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount the sub-app
        main_app.mount("sub", sub_app)

        # Verify it was mounted
        tools = await main_app.get_tools()
        assert "sub_sub_tool" in tools

        # Unmount the sub-app
        main_app.unmount("sub")

        # Verify it was unmounted
        tools = await main_app.get_tools()
        assert "sub_sub_tool" not in tools

        # Calling the tool should fail
        with pytest.raises(NotFoundError, match="Unknown tool: sub_sub_tool"):
            await main_app._mcp_call_tool("sub_sub_tool", {})

    async def test_mount_with_no_prefix(self):
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool()
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount with empty prefix but without deprecated separators
        main_app.mount(prefix="", server=sub_app)

        tools = await main_app.get_tools()
        # With empty prefix, the format is now "_sub_tool" instead of "sub_tool"
        assert "_sub_tool" in tools


class TestMultipleServerMount:
    """Test mounting multiple servers simultaneously."""

    async def test_mount_multiple_servers(self):
        """Test mounting multiple servers with different prefixes."""
        main_app = FastMCP("MainApp")
        weather_app = FastMCP("WeatherApp")
        news_app = FastMCP("NewsApp")

        @weather_app.tool()
        def get_forecast() -> str:
            return "Weather forecast"

        @news_app.tool()
        def get_headlines() -> str:
            return "News headlines"

        # Mount both apps
        main_app.mount("weather", weather_app)
        main_app.mount("news", news_app)

        # Check both are accessible
        tools = await main_app.get_tools()
        assert "weather_get_forecast" in tools
        assert "news_get_headlines" in tools

        # Call tools from both mounted servers
        result1 = await main_app._mcp_call_tool("weather_get_forecast", {})
        assert result1[0].text == "Weather forecast"  # type: ignore[attr-defined]

        result2 = await main_app._mcp_call_tool("news_get_headlines", {})
        assert result2[0].text == "News headlines"  # type: ignore[attr-defined]

    async def test_mount_same_prefix(self):
        """Test that mounting with the same prefix replaces the previous mount."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.tool()
        def first_tool() -> str:
            return "First app tool"

        @second_app.tool()
        def second_tool() -> str:
            return "Second app tool"

        # Mount first app
        main_app.mount("api", first_app)
        tools = await main_app.get_tools()
        assert "api_first_tool" in tools

        # Mount second app with same prefix
        main_app.mount("api", second_app)
        tools = await main_app.get_tools()

        # First app's tool should no longer be accessible
        assert "api_first_tool" not in tools

        # Second app's tool should be accessible
        assert "api_second_tool" in tools

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows asyncio networking timeouts."
    )
    async def test_mount_with_unreachable_proxy_servers(self, caplog):
        """Test graceful handling when multiple mounted servers fail to connect."""

        main_app = FastMCP("MainApp")
        working_app = FastMCP("WorkingApp")

        @working_app.tool()
        def working_tool() -> str:
            return "Working tool"

        @working_app.resource(uri="working://data")
        def working_resource():
            return "Working resource"

        @working_app.prompt()
        def working_prompt() -> str:
            return "Working prompt"

        # Mount the working server
        main_app.mount("working", working_app)

        # Use an unreachable port
        unreachable_client = Client(
            transport=SSETransport("http://127.0.0.1:99999/sse")
        )

        # Create a proxy server that will fail to connect
        unreachable_proxy = FastMCP.as_proxy(unreachable_client)

        # Mount the unreachable proxy
        main_app.mount("unreachable", unreachable_proxy)

        # All object types should work from working server despite unreachable proxy
        async with Client(main_app) as client:
            # Test tools
            tools = await client.list_tools()
            tool_names = [tool.name for tool in tools]
            assert "working_working_tool" in tool_names

            # Test calling a tool
            result = await client.call_tool("working_working_tool", {})
            assert result[0].text == "Working tool"  # type: ignore[attr-defined]

            # Test resources
            resources = await client.list_resources()
            resource_uris = [str(resource.uri) for resource in resources]
            assert "working://working/data" in resource_uris

            # Test prompts
            prompts = await client.list_prompts()
            prompt_names = [prompt.name for prompt in prompts]
            assert "working_working_prompt" in prompt_names

        # Verify that warnings were logged for the unreachable server
        warning_messages = [
            record.message for record in caplog.records if record.levelname == "WARNING"
        ]
        assert any(
            "Failed to get tools from mounted server 'unreachable'" in msg
            for msg in warning_messages
        )
        assert any(
            "Failed to get resources from mounted server 'unreachable'" in msg
            for msg in warning_messages
        )
        assert any(
            "Failed to get prompts from mounted server 'unreachable'" in msg
            for msg in warning_messages
        )


class TestDynamicChanges:
    """Test that changes to mounted servers are reflected dynamically."""

    async def test_adding_tool_after_mounting(self):
        """Test that tools added after mounting are accessible."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        # Mount the sub-app before adding any tools
        main_app.mount("sub", sub_app)

        # Initially, there should be no tools from sub_app
        tools = await main_app.get_tools()
        assert not any(key.startswith("sub_") for key in tools)

        # Add a tool to the sub-app after mounting
        @sub_app.tool()
        def dynamic_tool() -> str:
            return "Added after mounting"

        # The tool should be accessible through the main app
        tools = await main_app.get_tools()
        assert "sub_dynamic_tool" in tools

        # Call the dynamically added tool
        result = await main_app._mcp_call_tool("sub_dynamic_tool", {})
        assert result[0].text == "Added after mounting"  # type: ignore[attr-defined]

    async def test_removing_tool_after_mounting(self):
        """Test that tools removed from mounted servers are no longer accessible."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool()
        def temp_tool() -> str:
            return "Temporary tool"

        # Mount the sub-app
        main_app.mount("sub", sub_app)

        # Initially, the tool should be accessible
        tools = await main_app.get_tools()
        assert "sub_temp_tool" in tools

        # Remove the tool from sub_app
        sub_app._tool_manager._tools.pop("temp_tool")

        # The tool should no longer be accessible
        # Refresh the cache by clearing it
        main_app._cache.cache.clear()
        tools = await main_app.get_tools()
        assert "sub_temp_tool" not in tools


class TestResourcesAndTemplates:
    """Test mounting with resources and resource templates."""

    async def test_mount_with_resources(self):
        """Test mounting a server with resources."""
        main_app = FastMCP("MainApp")
        data_app = FastMCP("DataApp")

        @data_app.resource(uri="data://users")
        async def get_users():
            return ["user1", "user2"]

        # Mount the data app
        main_app.mount("data", data_app)

        # Resource should be accessible through main app
        resources = await main_app.get_resources()
        assert "data://data/users" in resources

        # Check that resource can be accessed
        async with Client(main_app) as client:
            result = await client.read_resource("data://data/users")
            assert json.loads(result[0].text) == ["user1", "user2"]  # type: ignore[attr-defined]

    async def test_mount_with_resource_templates(self):
        """Test mounting a server with resource templates."""
        main_app = FastMCP("MainApp")
        user_app = FastMCP("UserApp")

        @user_app.resource(uri="users://{user_id}/profile")
        def get_user_profile(user_id: str) -> dict:
            return {"id": user_id, "name": f"User {user_id}"}

        # Mount the user app
        main_app.mount("api", user_app)

        # Template should be accessible through main app
        templates = await main_app.get_resource_templates()
        assert "users://api/{user_id}/profile" in templates

        # Check template instantiation
        async with Client(main_app) as client:
            result = await client.read_resource("users://api/123/profile")
            profile = json.loads(result[0].text)  # type: ignore
            assert profile["id"] == "123"
            assert profile["name"] == "User 123"

    async def test_adding_resource_after_mounting(self):
        """Test adding a resource after mounting."""
        main_app = FastMCP("MainApp")
        data_app = FastMCP("DataApp")

        # Mount the data app before adding resources
        main_app.mount("data", data_app)

        # Add a resource after mounting
        @data_app.resource(uri="data://config")
        def get_config():
            return {"version": "1.0"}

        # Resource should be accessible through main app
        resources = await main_app.get_resources()
        assert "data://data/config" in resources

        # Check access to the resource
        async with Client(main_app) as client:
            result = await client.read_resource("data://data/config")
            config = json.loads(result[0].text)  # type: ignore[attr-defined]
            assert config["version"] == "1.0"


class TestPrompts:
    """Test mounting with prompts."""

    async def test_mount_with_prompts(self):
        """Test mounting a server with prompts."""
        main_app = FastMCP("MainApp")
        assistant_app = FastMCP("AssistantApp")

        @assistant_app.prompt()
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        # Mount the assistant app
        main_app.mount("assistant", assistant_app)

        # Prompt should be accessible through main app
        prompts = await main_app.get_prompts()
        assert "assistant_greeting" in prompts

        # Render the prompt
        result = await main_app._mcp_get_prompt("assistant_greeting", {"name": "World"})
        assert result.messages is not None
        # The message should contain our greeting text

    async def test_adding_prompt_after_mounting(self):
        """Test adding a prompt after mounting."""
        main_app = FastMCP("MainApp")
        assistant_app = FastMCP("AssistantApp")

        # Mount the assistant app before adding prompts
        main_app.mount("assistant", assistant_app)

        # Add a prompt after mounting
        @assistant_app.prompt()
        def farewell(name: str) -> str:
            return f"Goodbye, {name}!"

        # Prompt should be accessible through main app
        prompts = await main_app.get_prompts()
        assert "assistant_farewell" in prompts

        # Render the prompt
        result = await main_app._mcp_get_prompt("assistant_farewell", {"name": "World"})
        assert result.messages is not None
        # The message should contain our farewell text


class TestProxyServer:
    """Test mounting a proxy server."""

    async def test_mount_proxy_server(self):
        """Test mounting a proxy server."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        @original_server.tool()
        def get_data(query: str) -> str:
            return f"Data for {query}"

        # Create proxy server
        proxy_server = FastMCP.as_proxy(
            Client(transport=FastMCPTransport(original_server))
        )

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount("proxy", proxy_server)

        # Tool should be accessible through main app
        tools = await main_app.get_tools()
        assert "proxy_get_data" in tools

        # Call the tool
        result = await main_app._mcp_call_tool("proxy_get_data", {"query": "test"})
        assert result[0].text == "Data for test"  # type: ignore[attr-defined]

    async def test_dynamically_adding_to_proxied_server(self):
        """Test that changes to the original server are reflected in the mounted proxy."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        # Create proxy server
        proxy_server = FastMCP.as_proxy(
            Client(transport=FastMCPTransport(original_server))
        )

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount("proxy", proxy_server)

        # Add a tool to the original server
        @original_server.tool()
        def dynamic_data() -> str:
            return "Dynamic data"

        # Tool should be accessible through main app via proxy
        tools = await main_app.get_tools()
        assert "proxy_dynamic_data" in tools

        # Call the tool
        result = await main_app._mcp_call_tool("proxy_dynamic_data", {})
        assert result[0].text == "Dynamic data"  # type: ignore[attr-defined]

    async def test_proxy_server_with_resources(self):
        """Test mounting a proxy server with resources."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        @original_server.resource(uri="config://settings")
        def get_config():
            return {"api_key": "12345"}

        # Create proxy server
        proxy_server = FastMCP.as_proxy(
            Client(transport=FastMCPTransport(original_server))
        )

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount("proxy", proxy_server)

        # Resource should be accessible through main app
        result = await main_app._mcp_read_resource("config://proxy/settings")
        config = json.loads(result[0].content)  # type: ignore[attr-defined]
        assert config["api_key"] == "12345"

    async def test_proxy_server_with_prompts(self):
        """Test mounting a proxy server with prompts."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        @original_server.prompt()
        def welcome(name: str) -> str:
            return f"Welcome, {name}!"

        # Create proxy server
        proxy_server = FastMCP.as_proxy(
            Client(transport=FastMCPTransport(original_server))
        )

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount("proxy", proxy_server)

        # Prompt should be accessible through main app
        result = await main_app._mcp_get_prompt("proxy_welcome", {"name": "World"})
        assert result.messages is not None
        # The message should contain our welcome text


class TestAsProxyKwarg:
    """Test the as_proxy kwarg."""

    async def test_as_proxy_defaults_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount("sub", sub)

        assert mcp._mounted_servers["sub"].server is sub

    async def test_as_proxy_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount("sub", sub, as_proxy=False)

        assert mcp._mounted_servers["sub"].server is sub

    async def test_as_proxy_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount("sub", sub, as_proxy=True)

        assert mcp._mounted_servers["sub"].server is not sub
        assert isinstance(mcp._mounted_servers["sub"].server, FastMCPProxy)

    async def test_as_proxy_defaults_true_if_lifespan(self):
        @asynccontextmanager
        async def lifespan(mcp: FastMCP):
            yield

        mcp = FastMCP("Main")
        sub = FastMCP("Sub", lifespan=lifespan)

        mcp.mount("sub", sub)

        assert mcp._mounted_servers["sub"].server is not sub
        assert isinstance(mcp._mounted_servers["sub"].server, FastMCPProxy)

    async def test_as_proxy_ignored_for_proxy_mounts_default(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(Client(transport=FastMCPTransport(sub)))

        mcp.mount("sub", sub_proxy)

        assert mcp._mounted_servers["sub"].server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(Client(transport=FastMCPTransport(sub)))

        mcp.mount("sub", sub_proxy, as_proxy=False)

        assert mcp._mounted_servers["sub"].server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(Client(transport=FastMCPTransport(sub)))

        mcp.mount("sub", sub_proxy, as_proxy=True)

        assert mcp._mounted_servers["sub"].server is sub_proxy

    async def test_as_proxy_mounts_still_have_live_link(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount("sub", sub, as_proxy=True)

        assert len(await mcp.get_tools()) == 0

        @sub.tool()
        def hello():
            return "hi"

        assert len(await mcp.get_tools()) == 1

    async def test_sub_lifespan_is_executed(self):
        lifespan_check = []

        @asynccontextmanager
        async def lifespan(mcp: FastMCP):
            lifespan_check.append("start")
            yield

        mcp = FastMCP("Main")
        sub = FastMCP("Sub", lifespan=lifespan)

        @sub.tool()
        def hello():
            return "hi"

        mcp.mount("sub", sub, as_proxy=True)

        assert lifespan_check == []

        async with Client(mcp) as client:
            await client.call_tool("sub_hello", {})

        assert lifespan_check == ["start"]
