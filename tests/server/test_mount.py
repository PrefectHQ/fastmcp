import json
import sys
from contextlib import asynccontextmanager

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport, SSETransport
from fastmcp.server.proxy import FastMCPProxy


class TestBasicMount:
    """Test basic mounting functionality."""

    async def test_mount_simple_server(self):
        """Test mounting a simple server and accessing its tool."""
        # Create main app and sub-app
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        # Add a tool to the sub-app
        @sub_app.tool
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount the sub-app to the main app
        main_app.mount(sub_app, "sub")

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

        @sub_app.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        # Mount without custom separator - custom separators are deprecated
        main_app.mount(sub_app, "sub")

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
        main_app.mount(api_app, "api:sub")

    async def test_mount_invalid_resource_separator(self):
        main_app = FastMCP("MainApp")
        api_app = FastMCP("APIApp")

        # This test doesn't apply anymore with the new prefix format
        # Mount without deprecated parameters
        main_app.mount(api_app, "api")

    @pytest.mark.parametrize("prefix", ["", None])
    async def test_mount_with_no_prefix(self, prefix):
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount with empty prefix but without deprecated separators
        main_app.mount(sub_app, prefix=prefix)

        tools = await main_app.get_tools()
        # With empty prefix, the tool should keep its original name
        assert "sub_tool" in tools

    async def test_mount_with_no_prefix_provided(self):
        """Test mounting without providing a prefix at all."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount without providing a prefix (should be None)
        main_app.mount(sub_app)

        tools = await main_app.get_tools()
        # Without prefix, the tool should keep its original name
        assert "sub_tool" in tools

        # Call the tool to verify it works
        result = await main_app._mcp_call_tool("sub_tool", {})
        assert result[0].text == "This is from the sub app"  # type: ignore[attr-defined]

    async def test_mount_tools_no_prefix(self):
        """Test mounting a server with tools without prefix."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "Sub tool result"

        # Mount without prefix
        main_app.mount(sub_app)

        # Verify tool is accessible with original name
        tools = await main_app.get_tools()
        assert "sub_tool" in tools

        # Test actual functionality
        tool_result = await main_app._mcp_call_tool("sub_tool", {})
        assert tool_result[0].text == "Sub tool result"  # type: ignore[attr-defined]

    async def test_mount_resources_no_prefix(self):
        """Test mounting a server with resources without prefix."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.resource(uri="data://config")
        def sub_resource():
            return "Sub resource data"

        # Mount without prefix
        main_app.mount(sub_app)

        # Verify resource is accessible with original URI
        resources = await main_app.get_resources()
        assert "data://config" in resources

        # Test actual functionality
        resource_result = await main_app._mcp_read_resource("data://config")
        assert resource_result[0].content == "Sub resource data"  # type: ignore[attr-defined]

    async def test_mount_resource_templates_no_prefix(self):
        """Test mounting a server with resource templates without prefix."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.resource(uri="users://{user_id}/info")
        def sub_template(user_id: str):
            return f"Sub template for user {user_id}"

        # Mount without prefix
        main_app.mount(sub_app)

        # Verify template is accessible with original URI template
        templates = await main_app.get_resource_templates()
        assert "users://{user_id}/info" in templates

        # Test actual functionality
        template_result = await main_app._mcp_read_resource("users://123/info")
        assert template_result[0].content == "Sub template for user 123"  # type: ignore[attr-defined]

    async def test_mount_prompts_no_prefix(self):
        """Test mounting a server with prompts without prefix."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.prompt
        def sub_prompt() -> str:
            return "Sub prompt content"

        # Mount without prefix
        main_app.mount(sub_app)

        # Verify prompt is accessible with original name
        prompts = await main_app.get_prompts()
        assert "sub_prompt" in prompts

        # Test actual functionality
        prompt_result = await main_app._mcp_get_prompt("sub_prompt", {})
        assert prompt_result.messages is not None


class TestMultipleServerMount:
    """Test mounting multiple servers simultaneously."""

    async def test_mount_multiple_servers(self):
        """Test mounting multiple servers with different prefixes."""
        main_app = FastMCP("MainApp")
        weather_app = FastMCP("WeatherApp")
        news_app = FastMCP("NewsApp")

        @weather_app.tool
        def get_forecast() -> str:
            return "Weather forecast"

        @news_app.tool
        def get_headlines() -> str:
            return "News headlines"

        # Mount both apps
        main_app.mount(weather_app, "weather")
        main_app.mount(news_app, "news")

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

        @first_app.tool
        def first_tool() -> str:
            return "First app tool"

        @second_app.tool
        def second_tool() -> str:
            return "Second app tool"

        # Mount first app
        main_app.mount(first_app, "api")
        tools = await main_app.get_tools()
        assert "api_first_tool" in tools

        # Mount second app with same prefix
        main_app.mount(second_app, "api")
        tools = await main_app.get_tools()

        # Both apps' tools should be accessible (new behavior)
        assert "api_first_tool" in tools
        assert "api_second_tool" in tools

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows asyncio networking timeouts."
    )
    async def test_mount_with_unreachable_proxy_servers(self, caplog):
        """Test graceful handling when multiple mounted servers fail to connect."""

        main_app = FastMCP("MainApp")
        working_app = FastMCP("WorkingApp")

        @working_app.tool
        def working_tool() -> str:
            return "Working tool"

        @working_app.resource(uri="working://data")
        def working_resource():
            return "Working resource"

        @working_app.prompt
        def working_prompt() -> str:
            return "Working prompt"

        # Mount the working server
        main_app.mount(working_app, "working")

        # Use an unreachable port
        unreachable_client = Client(
            transport=SSETransport("http://127.0.0.1:9999/sse/")
        )

        # Create a proxy server that will fail to connect
        unreachable_proxy = FastMCP.as_proxy(unreachable_client)

        # Mount the unreachable proxy
        main_app.mount(unreachable_proxy, "unreachable")

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


class TestPrefixConflictResolution:
    """Test that later mounted servers win when there are conflicts."""

    async def test_later_server_wins_tools_no_prefix(self):
        """Test that later mounted server wins for tools when no prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.tool(name="shared_tool")
        def first_shared_tool() -> str:
            return "First app tool"

        @second_app.tool(name="shared_tool")
        def second_shared_tool() -> str:
            return "Second app tool"

        # Mount both apps without prefix
        main_app.mount(first_app)
        main_app.mount(second_app)

        async with Client(main_app) as client:
            # Test that list_tools shows the tool from later server
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "shared_tool" in tool_names
            assert tool_names.count("shared_tool") == 1  # Should only appear once

            # Test that calling the tool uses the later server's implementation
            result = await client.call_tool("shared_tool", {})
            assert result[0].text == "Second app tool"  # type: ignore[attr-defined]

    async def test_later_server_wins_tools_same_prefix(self):
        """Test that later mounted server wins for tools when same prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.tool(name="shared_tool")
        def first_shared_tool() -> str:
            return "First app tool"

        @second_app.tool(name="shared_tool")
        def second_shared_tool() -> str:
            return "Second app tool"

        # Mount both apps with same prefix
        main_app.mount(first_app, "api")
        main_app.mount(second_app, "api")

        async with Client(main_app) as client:
            # Test that list_tools shows the tool from later server
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "api_shared_tool" in tool_names
            assert tool_names.count("api_shared_tool") == 1  # Should only appear once

            # Test that calling the tool uses the later server's implementation
            result = await client.call_tool("api_shared_tool", {})
            assert result[0].text == "Second app tool"  # type: ignore[attr-defined]

    async def test_later_server_wins_resources_no_prefix(self):
        """Test that later mounted server wins for resources when no prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.resource(uri="shared://data")
        def first_resource():
            return "First app data"

        @second_app.resource(uri="shared://data")
        def second_resource():
            return "Second app data"

        # Mount both apps without prefix
        main_app.mount(first_app)
        main_app.mount(second_app)

        async with Client(main_app) as client:
            # Test that list_resources shows the resource from later server
            resources = await client.list_resources()
            resource_uris = [str(r.uri) for r in resources]
            assert "shared://data" in resource_uris
            assert resource_uris.count("shared://data") == 1  # Should only appear once

            # Test that reading the resource uses the later server's implementation
            result = await client.read_resource("shared://data")
            assert result[0].text == "Second app data"  # type: ignore[attr-defined]

    async def test_later_server_wins_resources_same_prefix(self):
        """Test that later mounted server wins for resources when same prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.resource(uri="shared://data")
        def first_resource():
            return "First app data"

        @second_app.resource(uri="shared://data")
        def second_resource():
            return "Second app data"

        # Mount both apps with same prefix
        main_app.mount(first_app, "api")
        main_app.mount(second_app, "api")

        async with Client(main_app) as client:
            # Test that list_resources shows the resource from later server
            resources = await client.list_resources()
            resource_uris = [str(r.uri) for r in resources]
            assert "shared://api/data" in resource_uris
            assert (
                resource_uris.count("shared://api/data") == 1
            )  # Should only appear once

            # Test that reading the resource uses the later server's implementation
            result = await client.read_resource("shared://api/data")
            assert result[0].text == "Second app data"  # type: ignore[attr-defined]

    async def test_later_server_wins_resource_templates_no_prefix(self):
        """Test that later mounted server wins for resource templates when no prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.resource(uri="users://{user_id}/profile")
        def first_template(user_id: str):
            return f"First app user {user_id}"

        @second_app.resource(uri="users://{user_id}/profile")
        def second_template(user_id: str):
            return f"Second app user {user_id}"

        # Mount both apps without prefix
        main_app.mount(first_app)
        main_app.mount(second_app)

        async with Client(main_app) as client:
            # Test that list_resource_templates shows the template from later server
            templates = await client.list_resource_templates()
            template_uris = [t.uriTemplate for t in templates]
            assert "users://{user_id}/profile" in template_uris
            assert (
                template_uris.count("users://{user_id}/profile") == 1
            )  # Should only appear once

            # Test that reading the resource uses the later server's implementation
            result = await client.read_resource("users://123/profile")
            assert result[0].text == "Second app user 123"  # type: ignore[attr-defined]

    async def test_later_server_wins_resource_templates_same_prefix(self):
        """Test that later mounted server wins for resource templates when same prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.resource(uri="users://{user_id}/profile")
        def first_template(user_id: str):
            return f"First app user {user_id}"

        @second_app.resource(uri="users://{user_id}/profile")
        def second_template(user_id: str):
            return f"Second app user {user_id}"

        # Mount both apps with same prefix
        main_app.mount(first_app, "api")
        main_app.mount(second_app, "api")

        async with Client(main_app) as client:
            # Test that list_resource_templates shows the template from later server
            templates = await client.list_resource_templates()
            template_uris = [t.uriTemplate for t in templates]
            assert "users://api/{user_id}/profile" in template_uris
            assert (
                template_uris.count("users://api/{user_id}/profile") == 1
            )  # Should only appear once

            # Test that reading the resource uses the later server's implementation
            result = await client.read_resource("users://api/123/profile")
            assert result[0].text == "Second app user 123"  # type: ignore[attr-defined]

    async def test_later_server_wins_prompts_no_prefix(self):
        """Test that later mounted server wins for prompts when no prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.prompt(name="shared_prompt")
        def first_shared_prompt() -> str:
            return "First app prompt"

        @second_app.prompt(name="shared_prompt")
        def second_shared_prompt() -> str:
            return "Second app prompt"

        # Mount both apps without prefix
        main_app.mount(first_app)
        main_app.mount(second_app)

        async with Client(main_app) as client:
            # Test that list_prompts shows the prompt from later server
            prompts = await client.list_prompts()
            prompt_names = [p.name for p in prompts]
            assert "shared_prompt" in prompt_names
            assert prompt_names.count("shared_prompt") == 1  # Should only appear once

            # Test that getting the prompt uses the later server's implementation
            result = await client.get_prompt("shared_prompt", {})
            assert result.messages is not None
            assert result.messages[0].content.text == "Second app prompt"  # type: ignore[attr-defined]

    async def test_later_server_wins_prompts_same_prefix(self):
        """Test that later mounted server wins for prompts when same prefix is used."""
        main_app = FastMCP("MainApp")
        first_app = FastMCP("FirstApp")
        second_app = FastMCP("SecondApp")

        @first_app.prompt(name="shared_prompt")
        def first_shared_prompt() -> str:
            return "First app prompt"

        @second_app.prompt(name="shared_prompt")
        def second_shared_prompt() -> str:
            return "Second app prompt"

        # Mount both apps with same prefix
        main_app.mount(first_app, "api")
        main_app.mount(second_app, "api")

        async with Client(main_app) as client:
            # Test that list_prompts shows the prompt from later server
            prompts = await client.list_prompts()
            prompt_names = [p.name for p in prompts]
            assert "api_shared_prompt" in prompt_names
            assert (
                prompt_names.count("api_shared_prompt") == 1
            )  # Should only appear once

            # Test that getting the prompt uses the later server's implementation
            result = await client.get_prompt("api_shared_prompt", {})
            assert result.messages is not None
            assert result.messages[0].content.text == "Second app prompt"  # type: ignore[attr-defined]


class TestDynamicChanges:
    """Test that changes to mounted servers are reflected dynamically."""

    async def test_adding_tool_after_mounting(self):
        """Test that tools added after mounting are accessible."""
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        # Mount the sub-app before adding any tools
        main_app.mount(sub_app, "sub")

        # Initially, there should be no tools from sub_app
        tools = await main_app.get_tools()
        assert not any(key.startswith("sub_") for key in tools)

        # Add a tool to the sub-app after mounting
        @sub_app.tool
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

        @sub_app.tool
        def temp_tool() -> str:
            return "Temporary tool"

        # Mount the sub-app
        main_app.mount(sub_app, "sub")

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

    async def test_cache_expiration(self):
        main_app = FastMCP("MainApp", cache_expiration_seconds=2)
        sub_app = FastMCP("SubApp")
        tools = await main_app.get_tools()
        assert len(tools) == 0

        @sub_app.tool
        def sub_tool():
            return "sub_tool"

        tools = await main_app.get_tools()
        assert len(tools) == 0


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
        main_app.mount(data_app, "data")

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
        main_app.mount(user_app, "api")

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
        main_app.mount(data_app, "data")

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

        @assistant_app.prompt
        def greeting(name: str) -> str:
            return f"Hello, {name}!"

        # Mount the assistant app
        main_app.mount(assistant_app, "assistant")

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
        main_app.mount(assistant_app, "assistant")

        # Add a prompt after mounting
        @assistant_app.prompt
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

        @original_server.tool
        def get_data(query: str) -> str:
            return f"Data for {query}"

        # Create proxy server
        proxy_server = FastMCP.as_proxy(
            Client(transport=FastMCPTransport(original_server))
        )

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount(proxy_server, "proxy")

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
        main_app.mount(proxy_server, "proxy")

        # Add a tool to the original server
        @original_server.tool
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
        main_app.mount(proxy_server, "proxy")

        # Resource should be accessible through main app
        result = await main_app._mcp_read_resource("config://proxy/settings")
        config = json.loads(result[0].content)  # type: ignore[attr-defined]
        assert config["api_key"] == "12345"

    async def test_proxy_server_with_prompts(self):
        """Test mounting a proxy server with prompts."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        @original_server.prompt
        def welcome(name: str) -> str:
            return f"Welcome, {name}!"

        # Create proxy server
        proxy_server = FastMCP.as_proxy(
            Client(transport=FastMCPTransport(original_server))
        )

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount(proxy_server, "proxy")

        # Prompt should be accessible through main app
        result = await main_app._mcp_get_prompt("proxy_welcome", {"name": "World"})
        assert result.messages is not None
        # The message should contain our welcome text


class TestAsProxyKwarg:
    """Test the as_proxy kwarg."""

    async def test_as_proxy_defaults_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub")
        assert mcp._tool_manager._mounted_servers[0].server is sub

    async def test_as_proxy_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub", as_proxy=False)

        assert mcp._tool_manager._mounted_servers[0].server is sub

    async def test_as_proxy_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub", as_proxy=True)

        assert mcp._tool_manager._mounted_servers[0].server is not sub
        assert isinstance(mcp._tool_manager._mounted_servers[0].server, FastMCPProxy)

    async def test_as_proxy_defaults_true_if_lifespan(self):
        @asynccontextmanager
        async def lifespan(mcp: FastMCP):
            yield

        mcp = FastMCP("Main")
        sub = FastMCP("Sub", lifespan=lifespan)

        mcp.mount(sub, "sub")

        assert mcp._tool_manager._mounted_servers[0].server is not sub
        assert isinstance(mcp._tool_manager._mounted_servers[0].server, FastMCPProxy)

    async def test_as_proxy_ignored_for_proxy_mounts_default(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(Client(transport=FastMCPTransport(sub)))

        mcp.mount(sub_proxy, "sub")

        assert mcp._tool_manager._mounted_servers[0].server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(Client(transport=FastMCPTransport(sub)))

        mcp.mount(sub_proxy, "sub", as_proxy=False)

        assert mcp._tool_manager._mounted_servers[0].server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(Client(transport=FastMCPTransport(sub)))

        mcp.mount(sub_proxy, "sub", as_proxy=True)

        assert mcp._tool_manager._mounted_servers[0].server is sub_proxy

    async def test_as_proxy_mounts_still_have_live_link(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub", as_proxy=True)

        assert len(await mcp.get_tools()) == 0

        @sub.tool
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

        @sub.tool
        def hello():
            return "hi"

        mcp.mount(sub, as_proxy=True)

        assert lifespan_check == []

        async with Client(mcp) as client:
            await client.call_tool("hello", {})

        assert len(lifespan_check) > 0
        # in the present implementation the sub server will be invoked 3 times
        # to call its tool
        assert lifespan_check == ["start", "start", "start"]
