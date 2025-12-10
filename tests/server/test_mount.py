import json
import sys
from contextlib import asynccontextmanager

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport, SSETransport
from fastmcp.server.proxy import FastMCPProxy
from fastmcp.tools.tool import Tool
from fastmcp.tools.tool_transform import TransformedTool
from fastmcp.utilities.tests import caplog_for_fastmcp


class TestBasicMount:
    """Test basic mounting functionality."""

    async def test_mount_simple_server(self):
        """Test mounting a simple server and accessing its tool."""
        # Create main app and sub-app
        main_app = FastMCP("MainApp")

        # Add a tool to the sub-app
        def tool() -> str:
            return "This is from the sub app"

        sub_tool = Tool.from_function(tool)

        transformed_tool = TransformedTool.from_tool(
            name="transformed_tool", tool=sub_tool
        )

        sub_app = FastMCP("SubApp", tools=[transformed_tool, sub_tool])

        # Mount the sub-app to the main app
        main_app.mount(sub_app, "sub")

        # Get tools from main app, should include sub_app's tools
        tools = await main_app.get_tools()
        assert "sub_tool" in tools
        assert "sub_transformed_tool" in tools

        async with Client(main_app) as client:
            result = await client.call_tool("sub_tool", {})
            assert result.data == "This is from the sub app"

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
        async with Client(main_app) as client:
            result = await client.call_tool("sub_greet", {"name": "World"})
            assert result.data == "Hello, World!"

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
        async with Client(main_app) as client:
            result = await client.call_tool("sub_tool", {})
            assert result.data == "This is from the sub app"

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
        async with Client(main_app) as client:
            tool_result = await client.call_tool("sub_tool", {})
            assert tool_result.data == "Sub tool result"

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
        async with Client(main_app) as client:
            resource_result = await client.read_resource("data://config")
            assert resource_result[0].text == "Sub resource data"  # type: ignore[attr-defined]

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
        async with Client(main_app) as client:
            template_result = await client.read_resource("users://123/info")
            assert template_result[0].text == "Sub template for user 123"  # type: ignore[attr-defined]

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
        async with Client(main_app) as client:
            prompt_result = await client.get_prompt("sub_prompt", {})
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
        async with Client(main_app) as client:
            result1 = await client.call_tool("weather_get_forecast", {})
            assert result1.data == "Weather forecast"
            result2 = await client.call_tool("news_get_headlines", {})
            assert result2.data == "News headlines"

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
            transport=SSETransport("http://127.0.0.1:9999/sse/"),
            name="unreachable_client",
        )

        # Create a proxy server that will fail to connect
        unreachable_proxy = FastMCP.as_proxy(
            unreachable_client, name="unreachable_proxy"
        )

        # Mount the unreachable proxy
        main_app.mount(unreachable_proxy, "unreachable")

        # All object types should work from working server despite unreachable proxy
        with caplog_for_fastmcp(caplog):
            async with Client(main_app, name="main_app_client") as client:
                # Test tools
                tools = await client.list_tools()
                tool_names = [tool.name for tool in tools]
                assert "working_working_tool" in tool_names

                # Test calling a tool
                result = await client.call_tool("working_working_tool", {})
                assert result.data == "Working tool"

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
            "Failed to list tools from mounted server 'unreachable_proxy'" in msg
            for msg in warning_messages
        )
        assert any(
            "Failed to list resources from 'unreachable_proxy'" in msg
            for msg in warning_messages
        )
        assert any(
            "Failed to list prompts from mounted server 'unreachable_proxy'" in msg
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
            assert result.data == "Second app tool"

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
            assert result.data == "Second app tool"

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
        async with Client(main_app) as client:
            result = await client.call_tool("sub_dynamic_tool", {})
            assert result.data == "Added after mounting"

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
        async with Client(main_app) as client:
            result = await client.get_prompt("assistant_greeting", {"name": "World"})
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
        async with Client(main_app) as client:
            result = await client.get_prompt("assistant_farewell", {"name": "World"})
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
        proxy_server = FastMCP.as_proxy(FastMCPTransport(original_server))

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount(proxy_server, "proxy")

        # Tool should be accessible through main app
        tools = await main_app.get_tools()
        assert "proxy_get_data" in tools

        # Call the tool
        async with Client(main_app) as client:
            result = await client.call_tool("proxy_get_data", {"query": "test"})
            assert result.data == "Data for test"

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
        tools = await main_app.get_tools()
        assert "proxy_dynamic_data" in tools

        # Call the tool
        async with Client(main_app) as client:
            result = await client.call_tool("proxy_dynamic_data", {})
            assert result.data == "Dynamic data"

    async def test_proxy_server_with_resources(self):
        """Test mounting a proxy server with resources."""
        # Create original server
        original_server = FastMCP("OriginalServer")

        @original_server.resource(uri="config://settings")
        def get_config():
            return {"api_key": "12345"}

        # Create proxy server
        proxy_server = FastMCP.as_proxy(FastMCPTransport(original_server))

        # Mount proxy server
        main_app = FastMCP("MainApp")
        main_app.mount(proxy_server, "proxy")

        # Resource should be accessible through main app
        async with Client(main_app) as client:
            result = await client.read_resource("config://proxy/settings")
            config = json.loads(result[0].text)  # type: ignore[attr-defined]
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
        async with Client(main_app) as client:
            result = await client.get_prompt("proxy_welcome", {"name": "World"})
            assert result.messages is not None
        # The message should contain our welcome text


class TestAsProxyKwarg:
    """Test the as_proxy kwarg."""

    async def test_as_proxy_defaults_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub")
        assert mcp._mounted_servers[0].server is sub

    async def test_as_proxy_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub", as_proxy=False)

        assert mcp._mounted_servers[0].server is sub

    async def test_as_proxy_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub", as_proxy=True)

        assert mcp._mounted_servers[0].server is not sub
        assert isinstance(mcp._mounted_servers[0].server, FastMCPProxy)

    async def test_as_proxy_defaults_true_if_lifespan(self):
        """Test that as_proxy defaults to True when server_lifespan is provided."""

        @asynccontextmanager
        async def server_lifespan(mcp: FastMCP):
            yield

        mcp = FastMCP("Main")
        sub = FastMCP("Sub", lifespan=server_lifespan)

        mcp.mount(sub, "sub")

        # Should auto-proxy because lifespan is set
        assert mcp._mounted_servers[0].server is not sub
        assert isinstance(mcp._mounted_servers[0].server, FastMCPProxy)

    async def test_as_proxy_ignored_for_proxy_mounts_default(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub")

        assert mcp._mounted_servers[0].server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub", as_proxy=False)

        assert mcp._mounted_servers[0].server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub", as_proxy=True)

        assert mcp._mounted_servers[0].server is sub_proxy

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

        # Lifespan is entered exactly once and kept alive by Docket worker
        assert lifespan_check == ["start"]


class TestResourceNamePrefixing:
    """Test that resource and resource template names get prefixed when mounted."""

    async def test_resource_name_prefixing(self):
        """Test that resource names are prefixed when mounted."""

        # Create a sub-app with a resource
        sub_app = FastMCP("SubApp")

        @sub_app.resource("resource://my_resource")
        def my_resource() -> str:
            return "Resource content"

        # Create main app and mount sub-app with prefix
        main_app = FastMCP("MainApp")
        main_app.mount(sub_app, "prefix")

        # Get resources from main app
        resources = await main_app.get_resources()

        # Should have prefixed key (using path format: resource://prefix/resource_name)
        assert "resource://prefix/my_resource" in resources

        # The resource name should also be prefixed
        resource = resources["resource://prefix/my_resource"]
        assert resource.name == "prefix_my_resource"

    async def test_resource_template_name_prefixing(self):
        """Test that resource template names are prefixed when mounted."""

        # Create a sub-app with a resource template
        sub_app = FastMCP("SubApp")

        @sub_app.resource("resource://user/{user_id}")
        def user_template(user_id: str) -> str:
            return f"User {user_id} data"

        # Create main app and mount sub-app with prefix
        main_app = FastMCP("MainApp")
        main_app.mount(sub_app, "prefix")

        # Get resource templates from main app
        templates = await main_app.get_resource_templates()

        # Should have prefixed key (using path format: resource://prefix/template_uri)
        assert "resource://prefix/user/{user_id}" in templates

        # The template name should also be prefixed
        template = templates["resource://prefix/user/{user_id}"]
        assert template.name == "prefix_user_template"


class TestParentTagFiltering:
    """Test that parent server tag filters apply recursively to mounted servers."""

    async def test_parent_include_tags_filters_mounted_tools(self):
        """Test that parent include_tags filters out non-matching mounted tools."""
        parent = FastMCP("Parent", include_tags={"allowed"})
        mounted = FastMCP("Mounted")

        @mounted.tool(tags={"allowed"})
        def allowed_tool() -> str:
            return "allowed"

        @mounted.tool(tags={"blocked"})
        def blocked_tool() -> str:
            return "blocked"

        parent.mount(mounted)

        async with Client(parent) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "allowed_tool" in tool_names
            assert "blocked_tool" not in tool_names

            # Verify execution also respects filters
            result = await client.call_tool("allowed_tool", {})
            assert result.data == "allowed"

            with pytest.raises(Exception, match="Unknown tool"):
                await client.call_tool("blocked_tool", {})

    async def test_parent_exclude_tags_filters_mounted_tools(self):
        """Test that parent exclude_tags filters out matching mounted tools."""
        parent = FastMCP("Parent", exclude_tags={"blocked"})
        mounted = FastMCP("Mounted")

        @mounted.tool(tags={"production"})
        def production_tool() -> str:
            return "production"

        @mounted.tool(tags={"blocked"})
        def blocked_tool() -> str:
            return "blocked"

        parent.mount(mounted)

        async with Client(parent) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "production_tool" in tool_names
            assert "blocked_tool" not in tool_names

    async def test_parent_filters_apply_to_mounted_resources(self):
        """Test that parent tag filters apply to mounted resources."""
        parent = FastMCP("Parent", include_tags={"allowed"})
        mounted = FastMCP("Mounted")

        @mounted.resource("resource://allowed", tags={"allowed"})
        def allowed_resource() -> str:
            return "allowed"

        @mounted.resource("resource://blocked", tags={"blocked"})
        def blocked_resource() -> str:
            return "blocked"

        parent.mount(mounted)

        async with Client(parent) as client:
            resources = await client.list_resources()
            resource_uris = {str(r.uri) for r in resources}
            assert "resource://allowed" in resource_uris
            assert "resource://blocked" not in resource_uris

    async def test_parent_filters_apply_to_mounted_prompts(self):
        """Test that parent tag filters apply to mounted prompts."""
        parent = FastMCP("Parent", exclude_tags={"blocked"})
        mounted = FastMCP("Mounted")

        @mounted.prompt(tags={"allowed"})
        def allowed_prompt() -> str:
            return "allowed"

        @mounted.prompt(tags={"blocked"})
        def blocked_prompt() -> str:
            return "blocked"

        parent.mount(mounted)

        async with Client(parent) as client:
            prompts = await client.list_prompts()
            prompt_names = {p.name for p in prompts}
            assert "allowed_prompt" in prompt_names
            assert "blocked_prompt" not in prompt_names


class TestCustomRouteForwarding:
    """Test that custom HTTP routes from mounted servers are forwarded."""

    async def test_get_additional_http_routes_empty(self):
        """Test _get_additional_http_routes returns empty list for server with no routes."""
        server = FastMCP("TestServer")
        routes = server._get_additional_http_routes()
        assert routes == []

    async def test_get_additional_http_routes_with_custom_route(self):
        """Test _get_additional_http_routes returns server's own routes."""
        server = FastMCP("TestServer")

        @server.custom_route("/test", methods=["GET"])
        async def test_route(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "test"})

        routes = server._get_additional_http_routes()
        assert len(routes) == 1
        assert routes[0].path == "/test"  # type: ignore[attr-defined]

    async def test_get_additional_http_routes_with_mounted_server(self):
        """Test _get_additional_http_routes includes routes from mounted servers."""
        main_server = FastMCP("MainServer")
        sub_server = FastMCP("SubServer")

        @sub_server.custom_route("/sub-route", methods=["GET"])
        async def sub_route(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "from sub"})

        # Mount the sub server
        main_server.mount(sub_server, "sub")

        routes = main_server._get_additional_http_routes()
        assert len(routes) == 1
        assert routes[0].path == "/sub-route"  # type: ignore[attr-defined]

    async def test_get_additional_http_routes_recursive(self):
        """Test _get_additional_http_routes works recursively with nested mounts."""
        main_server = FastMCP("MainServer")
        sub_server = FastMCP("SubServer")
        nested_server = FastMCP("NestedServer")

        @main_server.custom_route("/main-route", methods=["GET"])
        async def main_route(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "from main"})

        @sub_server.custom_route("/sub-route", methods=["GET"])
        async def sub_route(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "from sub"})

        @nested_server.custom_route("/nested-route", methods=["GET"])
        async def nested_route(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "from nested"})

        # Create nested mounting: main -> sub -> nested
        sub_server.mount(nested_server, "nested")
        main_server.mount(sub_server, "sub")

        routes = main_server._get_additional_http_routes()

        # Should include all routes
        assert len(routes) == 3
        route_paths = [route.path for route in routes]  # type: ignore[attr-defined]
        assert "/main-route" in route_paths
        assert "/sub-route" in route_paths
        assert "/nested-route" in route_paths

    async def test_mounted_servers_tracking(self):
        """Test that _mounted_servers list tracks mounted servers correctly."""
        main_server = FastMCP("MainServer")
        sub_server1 = FastMCP("SubServer1")
        sub_server2 = FastMCP("SubServer2")

        # Initially no mounted servers
        assert len(main_server._mounted_servers) == 0

        # Mount first server
        main_server.mount(sub_server1, "sub1")
        assert len(main_server._mounted_servers) == 1
        assert main_server._mounted_servers[0].server == sub_server1
        assert main_server._mounted_servers[0].prefix == "sub1"

        # Mount second server
        main_server.mount(sub_server2, "sub2")
        assert len(main_server._mounted_servers) == 2
        assert main_server._mounted_servers[1].server == sub_server2
        assert main_server._mounted_servers[1].prefix == "sub2"

    async def test_multiple_routes_same_server(self):
        """Test that multiple custom routes from same server are all included."""
        server = FastMCP("TestServer")

        @server.custom_route("/route1", methods=["GET"])
        async def route1(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "route1"})

        @server.custom_route("/route2", methods=["POST"])
        async def route2(request):
            from starlette.responses import JSONResponse

            return JSONResponse({"message": "route2"})

        routes = server._get_additional_http_routes()
        assert len(routes) == 2
        route_paths = [route.path for route in routes]  # type: ignore[attr-defined]
        assert "/route1" in route_paths
        assert "/route2" in route_paths


class TestDeeplyNestedMount:
    """Test deeply nested mount scenarios (3+ levels deep).

    This tests the fix for https://github.com/jlowin/fastmcp/issues/2583
    where tools/resources/prompts mounted more than 2 levels deep would fail
    to invoke even though they were correctly listed.
    """

    async def test_three_level_nested_tool_invocation(self):
        """Test invoking tools from servers mounted 3 levels deep."""
        root = FastMCP("root")
        middle = FastMCP("middle")
        leaf = FastMCP("leaf")

        @leaf.tool
        def add(a: int, b: int) -> int:
            return a + b

        @middle.tool
        def multiply(a: int, b: int) -> int:
            return a * b

        middle.mount(leaf, prefix="leaf")
        root.mount(middle, prefix="middle")

        async with Client(root) as client:
            # Tool at level 2 should work
            result = await client.call_tool("middle_multiply", {"a": 3, "b": 4})
            assert result.data == 12

            # Tool at level 3 should also work (this was the bug)
            result = await client.call_tool("middle_leaf_add", {"a": 5, "b": 7})
            assert result.data == 12

    async def test_three_level_nested_resource_invocation(self):
        """Test reading resources from servers mounted 3 levels deep."""
        root = FastMCP("root")
        middle = FastMCP("middle")
        leaf = FastMCP("leaf")

        @leaf.resource("leaf://data")
        def leaf_data() -> str:
            return "leaf data"

        @middle.resource("middle://data")
        def middle_data() -> str:
            return "middle data"

        middle.mount(leaf, prefix="leaf")
        root.mount(middle, prefix="middle")

        async with Client(root) as client:
            # Resource at level 2 should work
            result = await client.read_resource("middle://middle/data")
            assert result[0].text == "middle data"

            # Resource at level 3 should also work
            result = await client.read_resource("leaf://middle/leaf/data")
            assert result[0].text == "leaf data"

    async def test_three_level_nested_resource_template_invocation(self):
        """Test reading resource templates from servers mounted 3 levels deep."""
        root = FastMCP("root")
        middle = FastMCP("middle")
        leaf = FastMCP("leaf")

        @leaf.resource("leaf://item/{id}")
        def leaf_item(id: str) -> str:
            return f"leaf item {id}"

        @middle.resource("middle://item/{id}")
        def middle_item(id: str) -> str:
            return f"middle item {id}"

        middle.mount(leaf, prefix="leaf")
        root.mount(middle, prefix="middle")

        async with Client(root) as client:
            # Resource template at level 2 should work
            result = await client.read_resource("middle://middle/item/42")
            assert result[0].text == "middle item 42"

            # Resource template at level 3 should also work
            result = await client.read_resource("leaf://middle/leaf/item/99")
            assert result[0].text == "leaf item 99"

    async def test_three_level_nested_prompt_invocation(self):
        """Test getting prompts from servers mounted 3 levels deep."""
        root = FastMCP("root")
        middle = FastMCP("middle")
        leaf = FastMCP("leaf")

        @leaf.prompt
        def leaf_prompt(name: str) -> str:
            return f"Hello from leaf: {name}"

        @middle.prompt
        def middle_prompt(name: str) -> str:
            return f"Hello from middle: {name}"

        middle.mount(leaf, prefix="leaf")
        root.mount(middle, prefix="middle")

        async with Client(root) as client:
            # Prompt at level 2 should work
            result = await client.get_prompt("middle_middle_prompt", {"name": "World"})
            assert "Hello from middle: World" in result.messages[0].content.text  # type: ignore[union-attr]

            # Prompt at level 3 should also work
            result = await client.get_prompt(
                "middle_leaf_leaf_prompt", {"name": "Test"}
            )
            assert "Hello from leaf: Test" in result.messages[0].content.text  # type: ignore[union-attr]

    async def test_four_level_nested_tool_invocation(self):
        """Test invoking tools from servers mounted 4 levels deep."""
        root = FastMCP("root")
        level1 = FastMCP("level1")
        level2 = FastMCP("level2")
        level3 = FastMCP("level3")

        @level3.tool
        def deep_tool() -> str:
            return "very deep"

        level2.mount(level3, prefix="l3")
        level1.mount(level2, prefix="l2")
        root.mount(level1, prefix="l1")

        async with Client(root) as client:
            # Verify tool is listed
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "l1_l2_l3_deep_tool" in tool_names

            # Tool at level 4 should work
            result = await client.call_tool("l1_l2_l3_deep_tool", {})
            assert result.data == "very deep"
