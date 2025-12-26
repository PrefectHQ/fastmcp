import json
import sys
from contextlib import asynccontextmanager

import pytest
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport, SSETransport
from fastmcp.exceptions import NotFoundError
from fastmcp.server.providers import FastMCPProvider, TransformingProvider
from fastmcp.server.providers.proxy import FastMCPProxy
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
        assert any(t.name == "sub_tool" for t in tools)
        assert any(t.name == "sub_transformed_tool" for t in tools)

        result = await main_app.call_tool("sub_tool", {})
        assert result.structured_content == {"result": "This is from the sub app"}

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
        assert any(t.name == "sub_greet" for t in tools)

        # Call the tool
        result = await main_app.call_tool("sub_greet", {"name": "World"})
        assert result.structured_content == {"result": "Hello, World!"}

    @pytest.mark.parametrize("prefix", ["", None])
    async def test_mount_with_no_prefix(self, prefix):
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def sub_tool() -> str:
            return "This is from the sub app"

        # Mount with empty prefix but without deprecated separators
        main_app.mount(sub_app, namespace=prefix)

        tools = await main_app.get_tools()
        # With empty prefix, the tool should keep its original name
        assert any(t.name == "sub_tool" for t in tools)

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
        assert any(t.name == "sub_tool" for t in tools)

        # Call the tool to verify it works
        result = await main_app.call_tool("sub_tool", {})
        assert result.structured_content == {"result": "This is from the sub app"}

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
        assert any(t.name == "sub_tool" for t in tools)

        # Test actual functionality
        tool_result = await main_app.call_tool("sub_tool", {})
        assert tool_result.structured_content == {"result": "Sub tool result"}

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
        assert any(str(r.uri) == "data://config" for r in resources)

        # Test actual functionality
        resource_result = await main_app.read_resource("data://config")
        assert resource_result.contents[0].content == "Sub resource data"

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
        assert any(t.uri_template == "users://{user_id}/info" for t in templates)

        # Test actual functionality
        template_result = await main_app.read_resource("users://123/info")
        assert template_result.contents[0].content == "Sub template for user 123"

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
        assert any(p.name == "sub_prompt" for p in prompts)

        # Test actual functionality
        prompt_result = await main_app.render_prompt("sub_prompt")
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
        assert any(t.name == "weather_get_forecast" for t in tools)
        assert any(t.name == "news_get_headlines" for t in tools)

        # Call tools from both mounted servers
        result1 = await main_app.call_tool("weather_get_forecast", {})
        assert result1.structured_content == {"result": "Weather forecast"}
        result2 = await main_app.call_tool("news_get_headlines", {})
        assert result2.structured_content == {"result": "News headlines"}

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
        assert any(t.name == "api_first_tool" for t in tools)

        # Mount second app with same prefix
        main_app.mount(second_app, "api")
        tools = await main_app.get_tools()

        # Both apps' tools should be accessible (new behavior)
        assert any(t.name == "api_first_tool" for t in tools)
        assert any(t.name == "api_second_tool" for t in tools)

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

        # Verify that errors were logged for the unreachable provider
        error_messages = [
            record.message for record in caplog.records if record.levelname == "ERROR"
        ]
        assert any("Error listing tools from provider" in msg for msg in error_messages)
        assert any(
            "Error listing resources from provider" in msg for msg in error_messages
        )
        assert any(
            "Error listing prompts from provider" in msg for msg in error_messages
        )


class TestPrefixConflictResolution:
    """Test that first registered provider wins when there are conflicts.

    Provider semantics: 'Providers are queried in registration order; first non-None wins'
    """

    async def test_first_server_wins_tools_no_prefix(self):
        """Test that first mounted server wins for tools when no prefix is used."""
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

        # Test that get_tools shows the tool
        tools = await main_app.get_tools()
        tool_names = [t.name for t in tools]
        assert "shared_tool" in tool_names
        assert tool_names.count("shared_tool") == 1  # Should only appear once

        # Test that calling the tool uses the first server's implementation
        result = await main_app.call_tool("shared_tool", {})
        assert result.structured_content == {"result": "First app tool"}

    async def test_first_server_wins_tools_same_prefix(self):
        """Test that first mounted server wins for tools when same prefix is used."""
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

        # Test that get_tools shows the tool
        tools = await main_app.get_tools()
        tool_names = [t.name for t in tools]
        assert "api_shared_tool" in tool_names
        assert tool_names.count("api_shared_tool") == 1  # Should only appear once

        # Test that calling the tool uses the first server's implementation
        result = await main_app.call_tool("api_shared_tool", {})
        assert result.structured_content == {"result": "First app tool"}

    async def test_first_server_wins_resources_no_prefix(self):
        """Test that first mounted server wins for resources when no prefix is used."""
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

        # Test that get_resources shows the resource
        resources = await main_app.get_resources()
        resource_uris = [str(r.uri) for r in resources]
        assert "shared://data" in resource_uris
        assert resource_uris.count("shared://data") == 1  # Should only appear once

        # Test that reading the resource uses the first server's implementation
        result = await main_app.read_resource("shared://data")
        assert result.contents[0].content == "First app data"

    async def test_first_server_wins_resources_same_prefix(self):
        """Test that first mounted server wins for resources when same prefix is used."""
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

        # Test that get_resources shows the resource
        resources = await main_app.get_resources()
        resource_uris = [str(r.uri) for r in resources]
        assert "shared://api/data" in resource_uris
        assert resource_uris.count("shared://api/data") == 1  # Should only appear once

        # Test that reading the resource uses the first server's implementation
        result = await main_app.read_resource("shared://api/data")
        assert result.contents[0].content == "First app data"

    async def test_first_server_wins_resource_templates_no_prefix(self):
        """Test that first mounted server wins for resource templates when no prefix is used."""
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

        # Test that get_resource_templates shows the template
        templates = await main_app.get_resource_templates()
        template_uris = [t.uri_template for t in templates]
        assert "users://{user_id}/profile" in template_uris
        assert (
            template_uris.count("users://{user_id}/profile") == 1
        )  # Should only appear once

        # Test that reading the resource uses the first server's implementation
        result = await main_app.read_resource("users://123/profile")
        assert result.contents[0].content == "First app user 123"

    async def test_first_server_wins_resource_templates_same_prefix(self):
        """Test that first mounted server wins for resource templates when same prefix is used."""
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

        # Test that get_resource_templates shows the template
        templates = await main_app.get_resource_templates()
        template_uris = [t.uri_template for t in templates]
        assert "users://api/{user_id}/profile" in template_uris
        assert (
            template_uris.count("users://api/{user_id}/profile") == 1
        )  # Should only appear once

        # Test that reading the resource uses the first server's implementation
        result = await main_app.read_resource("users://api/123/profile")
        assert result.contents[0].content == "First app user 123"

    async def test_first_server_wins_prompts_no_prefix(self):
        """Test that first mounted server wins for prompts when no prefix is used."""
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

        # Test that get_prompts shows the prompt
        prompts = await main_app.get_prompts()
        prompt_names = [p.name for p in prompts]
        assert "shared_prompt" in prompt_names
        assert prompt_names.count("shared_prompt") == 1  # Should only appear once

        # Test that getting the prompt uses the first server's implementation
        result = await main_app.render_prompt("shared_prompt")
        assert result.messages is not None
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "First app prompt"

    async def test_first_server_wins_prompts_same_prefix(self):
        """Test that first mounted server wins for prompts when same prefix is used."""
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

        # Test that get_prompts shows the prompt
        prompts = await main_app.get_prompts()
        prompt_names = [p.name for p in prompts]
        assert "api_shared_prompt" in prompt_names
        assert prompt_names.count("api_shared_prompt") == 1  # Should only appear once

        # Test that getting the prompt uses the first server's implementation
        result = await main_app.render_prompt("api_shared_prompt")
        assert result.messages is not None
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "First app prompt"


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
        assert not any(t.name.startswith("sub_") for t in tools)

        # Add a tool to the sub-app after mounting
        @sub_app.tool
        def dynamic_tool() -> str:
            return "Added after mounting"

        # The tool should be accessible through the main app
        tools = await main_app.get_tools()
        assert any(t.name == "sub_dynamic_tool" for t in tools)

        # Call the dynamically added tool
        result = await main_app.call_tool("sub_dynamic_tool", {})
        assert result.structured_content == {"result": "Added after mounting"}

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
        assert any(t.name == "sub_temp_tool" for t in tools)

        # Remove the tool from sub_app using public API
        sub_app.remove_tool("temp_tool")

        # The tool should no longer be accessible
        tools = await main_app.get_tools()
        assert not any(t.name == "sub_temp_tool" for t in tools)


class TestResourcesAndTemplates:
    """Test mounting with resources and resource templates."""

    async def test_mount_with_resources(self):
        """Test mounting a server with resources."""
        main_app = FastMCP("MainApp")
        data_app = FastMCP("DataApp")

        @data_app.resource(uri="data://users")
        async def get_users() -> str:
            return "user1, user2"

        # Mount the data app
        main_app.mount(data_app, "data")

        # Resource should be accessible through main app
        resources = await main_app.get_resources()
        assert any(str(r.uri) == "data://data/users" for r in resources)

        # Check that resource can be accessed
        result = await main_app.read_resource("data://data/users")
        assert len(result.contents) == 1
        # Note: The function returns "user1, user2" which is not valid JSON
        # This test should be updated to return proper JSON or check the string directly
        assert result.contents[0].content == "user1, user2"

    async def test_mount_with_resource_templates(self):
        """Test mounting a server with resource templates."""
        main_app = FastMCP("MainApp")
        user_app = FastMCP("UserApp")

        @user_app.resource(uri="users://{user_id}/profile")
        def get_user_profile(user_id: str) -> str:
            return json.dumps({"id": user_id, "name": f"User {user_id}"})

        # Mount the user app
        main_app.mount(user_app, "api")

        # Template should be accessible through main app
        templates = await main_app.get_resource_templates()
        assert any(t.uri_template == "users://api/{user_id}/profile" for t in templates)

        # Check template instantiation
        result = await main_app.read_resource("users://api/123/profile")
        assert len(result.contents) == 1
        profile = json.loads(result.contents[0].content)
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
        def get_config() -> str:
            return json.dumps({"version": "1.0"})

        # Resource should be accessible through main app
        resources = await main_app.get_resources()
        assert any(str(r.uri) == "data://data/config" for r in resources)

        # Check access to the resource
        result = await main_app.read_resource("data://data/config")
        assert len(result.contents) == 1
        config = json.loads(result.contents[0].content)
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
        assert any(p.name == "assistant_greeting" for p in prompts)

        # Render the prompt
        result = await main_app.render_prompt("assistant_greeting", {"name": "World"})
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
        assert any(p.name == "assistant_farewell" for p in prompts)

        # Render the prompt
        result = await main_app.render_prompt("assistant_farewell", {"name": "World"})
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
        tools = await main_app.get_tools()
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

        mcp.mount(sub, "sub")
        # Index 1 because LocalProvider is at index 0
        provider = mcp._providers[1]
        # With namespace, we get TransformingProvider wrapping FastMCPProvider
        assert isinstance(provider, TransformingProvider)
        assert isinstance(provider._wrapped, FastMCPProvider)
        assert provider._wrapped.server is sub

    async def test_as_proxy_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub", as_proxy=False)

        # Index 1 because LocalProvider is at index 0
        provider = mcp._providers[1]
        # With namespace, we get TransformingProvider wrapping FastMCPProvider
        assert isinstance(provider, TransformingProvider)
        assert isinstance(provider._wrapped, FastMCPProvider)
        assert provider._wrapped.server is sub

    async def test_as_proxy_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")

        mcp.mount(sub, "sub", as_proxy=True)

        # Index 1 because LocalProvider is at index 0
        provider = mcp._providers[1]
        # With namespace, we get TransformingProvider wrapping FastMCPProvider
        assert isinstance(provider, TransformingProvider)
        assert isinstance(provider._wrapped, FastMCPProvider)
        assert provider._wrapped.server is not sub
        assert isinstance(provider._wrapped.server, FastMCPProxy)

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

        mcp.mount(sub, "sub")

        # Server should be mounted directly without auto-proxying
        # Index 1 because LocalProvider is at index 0
        provider = mcp._providers[1]
        assert isinstance(provider, TransformingProvider)
        assert isinstance(provider._wrapped, FastMCPProvider)
        assert provider._wrapped.server is sub

    async def test_as_proxy_ignored_for_proxy_mounts_default(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub")

        # Index 1 because LocalProvider is at index 0
        provider = mcp._providers[1]
        assert isinstance(provider, TransformingProvider)
        assert isinstance(provider._wrapped, FastMCPProvider)
        assert provider._wrapped.server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_false(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub", as_proxy=False)

        # Index 1 because LocalProvider is at index 0
        provider = mcp._providers[1]
        assert isinstance(provider, TransformingProvider)
        assert isinstance(provider._wrapped, FastMCPProvider)
        assert provider._wrapped.server is sub_proxy

    async def test_as_proxy_ignored_for_proxy_mounts_true(self):
        mcp = FastMCP("Main")
        sub = FastMCP("Sub")
        sub_proxy = FastMCP.as_proxy(FastMCPTransport(sub))

        mcp.mount(sub_proxy, "sub", as_proxy=True)

        # Index 1 because LocalProvider is at index 0
        provider = mcp._providers[1]
        assert isinstance(provider, TransformingProvider)
        assert isinstance(provider._wrapped, FastMCPProvider)
        assert provider._wrapped.server is sub_proxy

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


class TestResourceUriPrefixing:
    """Test that resource and resource template URIs get prefixed when mounted (names are NOT prefixed)."""

    async def test_resource_uri_prefixing(self):
        """Test that resource URIs are prefixed when mounted (names are NOT prefixed)."""

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
        assert any(str(r.uri) == "resource://prefix/my_resource" for r in resources)

        # The resource name should NOT be prefixed (only URI is prefixed)
        resource = next(
            r for r in resources if str(r.uri) == "resource://prefix/my_resource"
        )
        assert resource.name == "my_resource"

    async def test_resource_template_uri_prefixing(self):
        """Test that resource template URIs are prefixed when mounted (names are NOT prefixed)."""

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
        assert any(
            t.uri_template == "resource://prefix/user/{user_id}" for t in templates
        )

        # The template name should NOT be prefixed (only URI template is prefixed)
        template = next(
            t for t in templates if t.uri_template == "resource://prefix/user/{user_id}"
        )
        assert template.name == "user_template"


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

        tools = await parent.get_tools()
        tool_names = {t.name for t in tools}
        assert "allowed_tool" in tool_names
        assert "blocked_tool" not in tool_names

        # Verify execution also respects filters
        result = await parent.call_tool("allowed_tool", {})
        assert result.structured_content == {"result": "allowed"}

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await parent.call_tool("blocked_tool", {})

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

        tools = await parent.get_tools()
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

        resources = await parent.get_resources()
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

        prompts = await parent.get_prompts()
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
        assert hasattr(routes[0], "path")
        assert routes[0].path == "/test"

    async def test_mounted_servers_tracking(self):
        """Test that _providers list tracks mounted servers correctly."""
        from fastmcp.server.providers.local_provider import LocalProvider

        main_server = FastMCP("MainServer")
        sub_server1 = FastMCP("SubServer1")
        sub_server2 = FastMCP("SubServer2")

        # Initially only LocalProvider
        assert len(main_server._providers) == 1
        assert isinstance(main_server._providers[0], LocalProvider)

        # Mount first server
        main_server.mount(sub_server1, "sub1")
        assert len(main_server._providers) == 2
        # LocalProvider is at index 0, mounted provider at index 1
        provider1 = main_server._providers[1]
        assert isinstance(provider1, TransformingProvider)
        assert isinstance(provider1._wrapped, FastMCPProvider)
        assert provider1._wrapped.server == sub_server1
        assert provider1.namespace == "sub1"

        # Mount second server
        main_server.mount(sub_server2, "sub2")
        assert len(main_server._providers) == 3
        provider2 = main_server._providers[2]
        assert isinstance(provider2, TransformingProvider)
        assert isinstance(provider2._wrapped, FastMCPProvider)
        assert provider2._wrapped.server == sub_server2
        assert provider2.namespace == "sub2"

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
        route_paths = [route.path for route in routes if hasattr(route, "path")]
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

        middle.mount(leaf, namespace="leaf")
        root.mount(middle, namespace="middle")

        # Tool at level 2 should work
        result = await root.call_tool("middle_multiply", {"a": 3, "b": 4})
        assert result.structured_content == {"result": 12}

        # Tool at level 3 should also work (this was the bug)
        result = await root.call_tool("middle_leaf_add", {"a": 5, "b": 7})
        assert result.structured_content == {"result": 12}

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

        middle.mount(leaf, namespace="leaf")
        root.mount(middle, namespace="middle")

        # Resource at level 2 should work
        result = await root.read_resource("middle://middle/data")
        assert result.contents[0].content == "middle data"

        # Resource at level 3 should also work
        result = await root.read_resource("leaf://middle/leaf/data")
        assert result.contents[0].content == "leaf data"

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

        middle.mount(leaf, namespace="leaf")
        root.mount(middle, namespace="middle")

        # Resource template at level 2 should work
        result = await root.read_resource("middle://middle/item/42")
        assert result.contents[0].content == "middle item 42"

        # Resource template at level 3 should also work
        result = await root.read_resource("leaf://middle/leaf/item/99")
        assert result.contents[0].content == "leaf item 99"

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

        middle.mount(leaf, namespace="leaf")
        root.mount(middle, namespace="middle")

        # Prompt at level 2 should work
        result = await root.render_prompt("middle_middle_prompt", {"name": "World"})
        assert isinstance(result.messages[0].content, TextContent)
        assert "Hello from middle: World" in result.messages[0].content.text

        # Prompt at level 3 should also work
        result = await root.render_prompt("middle_leaf_leaf_prompt", {"name": "Test"})
        assert isinstance(result.messages[0].content, TextContent)
        assert "Hello from leaf: Test" in result.messages[0].content.text

    async def test_four_level_nested_tool_invocation(self):
        """Test invoking tools from servers mounted 4 levels deep."""
        root = FastMCP("root")
        level1 = FastMCP("level1")
        level2 = FastMCP("level2")
        level3 = FastMCP("level3")

        @level3.tool
        def deep_tool() -> str:
            return "very deep"

        level2.mount(level3, namespace="l3")
        level1.mount(level2, namespace="l2")
        root.mount(level1, namespace="l1")

        # Verify tool is listed
        tools = await root.get_tools()
        tool_names = [t.name for t in tools]
        assert "l1_l2_l3_deep_tool" in tool_names

        # Tool at level 4 should work
        result = await root.call_tool("l1_l2_l3_deep_tool", {})
        assert result.structured_content == {"result": "very deep"}


class TestToolNameOverrides:
    """Test tool and prompt name overrides in mount() (issue #2596)."""

    async def test_tool_names_override_via_transforms(self):
        """Test that tool_names renames tools via TransformingProvider.

        With TransformingProvider, tool_renames are applied to the original name
        and bypass namespace prefixing. Both server introspection and client-facing
        API show the transformed names consistently.
        """
        sub = FastMCP("Sub")

        @sub.tool
        def original_tool() -> str:
            return "test"

        main = FastMCP("Main")
        # tool_names maps original name → final name (bypasses namespace)
        main.mount(
            sub,
            namespace="prefix",
            tool_names={"original_tool": "custom_name"},
        )

        # Server introspection shows transformed names
        tools = await main.get_tools()
        tool_names = [t.name for t in tools]
        assert "custom_name" in tool_names
        assert "original_tool" not in tool_names
        assert "prefix_original_tool" not in tool_names

    async def test_tool_names_override_applied_in_list_tools(self):
        """Test that tool_names override is reflected in list_tools()."""
        sub = FastMCP("Sub")

        @sub.tool
        def original_tool() -> str:
            return "test"

        main = FastMCP("Main")
        main.mount(
            sub,
            namespace="prefix",
            tool_names={"original_tool": "custom_name"},
        )

        tools = await main.get_tools()
        tool_names = [t.name for t in tools]
        assert "custom_name" in tool_names
        assert "prefix_original_tool" not in tool_names

    async def test_tool_call_with_overridden_name(self):
        """Test that overridden tool can be called by its new name."""
        sub = FastMCP("Sub")

        @sub.tool
        def original_tool() -> str:
            return "success"

        main = FastMCP("Main")
        main.mount(
            sub,
            namespace="prefix",
            tool_names={"original_tool": "renamed"},
        )

        result = await main.call_tool("renamed", {})
        assert result.structured_content == {"result": "success"}

    def test_duplicate_tool_rename_targets_raises_error(self):
        """Test that duplicate target names in tool_renames raises ValueError."""
        sub = FastMCP("Sub")
        main = FastMCP("Main")

        with pytest.raises(ValueError, match="duplicate target name"):
            main.mount(
                sub,
                tool_names={"tool_a": "same_name", "tool_b": "same_name"},
            )


class TestMountedServerDocketBehavior:
    """Regression tests for mounted server lifecycle behavior.

    These tests guard against architectural changes that could accidentally
    start Docket instances for mounted servers. Mounted servers should only
    run their user-defined lifespan, not the full _lifespan_manager which
    includes Docket creation.
    """

    async def test_mounted_server_does_not_have_docket(self):
        """Test that a mounted server doesn't create its own Docket.

        MountedProvider.lifespan() should call only the server's _lifespan
        (user-defined lifespan), not _lifespan_manager (which includes Docket).
        """
        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def my_tool() -> str:
            return "test"

        main_app.mount(sub_app, "sub")

        # After running the main app's lifespan, the sub app should not have
        # its own Docket instance
        async with Client(main_app) as client:
            # The main app should have a docket (created by _lifespan_manager)
            assert main_app.docket is not None

            # The mounted sub app should NOT have its own docket
            # It uses the parent's docket for background tasks
            assert sub_app.docket is None

            # But the tool should still work (prefixed as sub_my_tool)
            result = await client.call_tool("sub_my_tool", {})
            assert result.data == "test"


class TestComponentServicePrefixLess:
    """Test that ComponentService works with prefix-less mounted servers."""

    async def test_enable_tool_prefixless_mount(self):
        """Test enabling a tool on a prefix-less mounted server."""
        from fastmcp.contrib.component_manager.component_service import ComponentService

        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.tool
        def my_tool() -> str:
            return "test"

        # Mount without prefix
        main_app.mount(sub_app)

        # Initially the tool is enabled
        tools = await main_app.get_tools()
        assert any(t.name == "my_tool" for t in tools)

        # Disable and re-enable via ComponentService
        service = ComponentService(main_app)
        tool = await service._disable_tool("my_tool")
        assert tool is not None
        # Verify tool is now disabled
        tools = await main_app.get_tools()
        assert not any(t.name == "my_tool" for t in tools)

        tool = await service._enable_tool("my_tool")
        assert tool is not None
        # Verify tool is now enabled
        tools = await main_app.get_tools()
        assert any(t.name == "my_tool" for t in tools)

    async def test_enable_resource_prefixless_mount(self):
        """Test enabling a resource on a prefix-less mounted server."""
        from fastmcp.contrib.component_manager.component_service import ComponentService

        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.resource(uri="data://test")
        def my_resource() -> str:
            return "test data"

        # Mount without prefix
        main_app.mount(sub_app)

        # Disable and re-enable via ComponentService
        service = ComponentService(main_app)
        resource = await service._disable_resource("data://test")
        assert resource is not None
        # Verify resource is now disabled
        resources = await main_app.get_resources()
        assert not any(str(r.uri) == "data://test" for r in resources)

        resource = await service._enable_resource("data://test")
        assert resource is not None
        # Verify resource is now enabled
        resources = await main_app.get_resources()
        assert any(str(r.uri) == "data://test" for r in resources)

    async def test_enable_prompt_prefixless_mount(self):
        """Test enabling a prompt on a prefix-less mounted server."""
        from fastmcp.contrib.component_manager.component_service import ComponentService

        main_app = FastMCP("MainApp")
        sub_app = FastMCP("SubApp")

        @sub_app.prompt
        def my_prompt() -> str:
            return "test prompt"

        # Mount without prefix
        main_app.mount(sub_app)

        # Disable and re-enable via ComponentService
        service = ComponentService(main_app)
        prompt = await service._disable_prompt("my_prompt")
        assert prompt is not None
        # Verify prompt is now disabled
        prompts = await main_app.get_prompts()
        assert not any(p.name == "my_prompt" for p in prompts)

        prompt = await service._enable_prompt("my_prompt")
        assert prompt is not None
        # Verify prompt is now enabled
        prompts = await main_app.get_prompts()
        assert any(p.name == "my_prompt" for p in prompts)
