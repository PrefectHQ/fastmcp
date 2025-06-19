import json
from urllib.parse import quote

from fastmcp.client.client import Client
from fastmcp.server.server import FastMCP
from fastmcp.tools.tool import FunctionTool, Tool


async def test_import_basic_functionality():
    """Test that the import method properly imports tools and other resources."""
    # Create main app and sub-app
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    # Add a tool to the sub-app
    @sub_app.tool
    def sub_tool() -> str:
        return "This is from the sub app"

    # Import the sub-app to the main app
    await main_app.import_server(sub_app, "sub")

    # Verify the tool was imported with the prefix
    assert "sub_sub_tool" in main_app._tool_manager._tools
    assert "sub_tool" in sub_app._tool_manager._tools

    # Verify the original tool still exists in the sub-app
    tool = await main_app._tool_manager.get_tool("sub_sub_tool")
    assert tool is not None
    assert tool.name == "sub_tool"
    assert isinstance(tool, FunctionTool)
    assert callable(tool.fn)


async def test_import_multiple_apps():
    """Test importing multiple apps to a main app."""
    # Create main app and multiple sub-apps
    main_app = FastMCP("MainApp")
    weather_app = FastMCP("WeatherApp")
    news_app = FastMCP("NewsApp")

    # Add tools to each sub-app
    @weather_app.tool
    def get_forecast() -> str:
        return "Weather forecast"

    @news_app.tool
    def get_headlines() -> str:
        return "News headlines"

    # Import both sub-apps to the main app
    await main_app.import_server(weather_app, "weather")
    await main_app.import_server(news_app, "news")

    # Verify tools were imported with the correct prefixes
    assert "weather_get_forecast" in main_app._tool_manager._tools
    assert "news_get_headlines" in main_app._tool_manager._tools


async def test_import_combines_tools():
    """Test that importing preserves existing tools with the same prefix."""
    # Create apps
    main_app = FastMCP("MainApp")
    first_app = FastMCP("FirstApp")
    second_app = FastMCP("SecondApp")

    # Add tools to each sub-app
    @first_app.tool
    def first_tool() -> str:
        return "First app tool"

    @second_app.tool
    def second_tool() -> str:
        return "Second app tool"

    # Import first app
    await main_app.import_server(first_app, "api")
    assert "api_first_tool" in main_app._tool_manager._tools

    # Import second app to same prefix
    await main_app.import_server(second_app, "api")

    # Verify second tool is there
    assert "api_second_tool" in main_app._tool_manager._tools

    # Tools from both imports are combined
    assert "api_first_tool" in main_app._tool_manager._tools


async def test_import_with_resources():
    """Test importing with resources."""
    # Create apps
    main_app = FastMCP("MainApp")
    data_app = FastMCP("DataApp")

    # Add a resource to the data app
    @data_app.resource(uri="data://users")
    async def get_users():
        return ["user1", "user2"]

    # Import the data app
    await main_app.import_server(data_app, "data")

    # Verify the resource was imported with the prefix
    assert "data://data/users" in main_app._resource_manager._resources


async def test_import_with_resource_templates():
    """Test importing with resource templates."""
    # Create apps
    main_app = FastMCP("MainApp")
    user_app = FastMCP("UserApp")

    # Add a resource template to the user app
    @user_app.resource(uri="users://{user_id}/profile")
    def get_user_profile(user_id: str) -> dict:
        return {"id": user_id, "name": f"User {user_id}"}

    # Import the user app
    await main_app.import_server(user_app, "api")

    # Verify the template was imported with the prefix
    assert "users://api/{user_id}/profile" in main_app._resource_manager._templates


async def test_import_with_prompts():
    """Test importing with prompts."""
    # Create apps
    main_app = FastMCP("MainApp")
    assistant_app = FastMCP("AssistantApp")

    # Add a prompt to the assistant app
    @assistant_app.prompt
    def greeting(name: str) -> str:
        return f"Hello, {name}!"

    # Import the assistant app
    await main_app.import_server(assistant_app, "assistant")

    # Verify the prompt was imported with the prefix
    assert "assistant_greeting" in main_app._prompt_manager._prompts


async def test_import_multiple_resource_templates():
    """Test importing multiple apps with resource templates."""
    # Create apps
    main_app = FastMCP("MainApp")
    weather_app = FastMCP("WeatherApp")
    news_app = FastMCP("NewsApp")

    # Add templates to each app
    @weather_app.resource(uri="weather://{city}")
    def get_weather(city: str) -> str:
        return f"Weather for {city}"

    @news_app.resource(uri="news://{category}")
    def get_news(category: str) -> str:
        return f"News for {category}"

    # Import both apps
    await main_app.import_server(weather_app, "data")
    await main_app.import_server(news_app, "content")

    # Verify templates were imported with correct prefixes
    assert "weather://data/{city}" in main_app._resource_manager._templates
    assert "news://content/{category}" in main_app._resource_manager._templates


async def test_import_multiple_prompts():
    """Test importing multiple apps with prompts."""
    # Create apps
    main_app = FastMCP("MainApp")
    python_app = FastMCP("PythonApp")
    sql_app = FastMCP("SQLApp")

    # Add prompts to each app
    @python_app.prompt
    def review_python(code: str) -> str:
        return f"Reviewing Python code:\n{code}"

    @sql_app.prompt
    def explain_sql(query: str) -> str:
        return f"Explaining SQL query:\n{query}"

    # Import both apps
    await main_app.import_server(python_app, "python")
    await main_app.import_server(sql_app, "sql")

    # Verify prompts were imported with correct prefixes
    assert "python_review_python" in main_app._prompt_manager._prompts
    assert "sql_explain_sql" in main_app._prompt_manager._prompts


async def test_tool_custom_name_preserved_when_imported():
    """Test that a tool's custom name is preserved when imported."""
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    def fetch_data(query: str) -> str:
        return f"Data for query: {query}"

    api_app.add_tool(Tool.from_function(fetch_data, name="get_data"))
    await main_app.import_server(api_app, "api")

    # Check that the tool is accessible by its prefixed name
    tool = await main_app._tool_manager.get_tool("api_get_data")
    assert tool is not None

    # Check that the function name is preserved
    assert isinstance(tool, FunctionTool)
    assert tool.fn.__name__ == "fetch_data"


async def test_call_imported_custom_named_tool():
    """Test calling an imported tool with a custom name."""
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    def fetch_data(query: str) -> str:
        return f"Data for query: {query}"

    api_app.add_tool(Tool.from_function(fetch_data, name="get_data"))
    await main_app.import_server(api_app, "api")

    async with Client(main_app) as client:
        result = await client.call_tool("api_get_data", {"query": "test"})
        assert result[0].text == "Data for query: test"  # type: ignore[attr-defined]


async def test_first_level_importing_with_custom_name():
    """Test that a tool with a custom name is correctly imported at the first level."""
    service_app = FastMCP("ServiceApp")
    provider_app = FastMCP("ProviderApp")

    def calculate_value(input: int) -> int:
        return input * 2

    provider_app.add_tool(Tool.from_function(calculate_value, name="compute"))
    await service_app.import_server(provider_app, "provider")

    # Tool is accessible in the service app with the first prefix
    tool = await service_app._tool_manager.get_tool("provider_compute")
    assert tool is not None
    assert isinstance(tool, FunctionTool)
    assert tool.fn.__name__ == "calculate_value"


async def test_nested_importing_preserves_prefixes():
    """Test that importing a previously imported app preserves prefixes."""
    main_app = FastMCP("MainApp")
    service_app = FastMCP("ServiceApp")
    provider_app = FastMCP("ProviderApp")

    def calculate_value(input: int) -> int:
        return input * 2

    provider_app.add_tool(Tool.from_function(calculate_value, name="compute"))
    await service_app.import_server(provider_app, "provider")
    await main_app.import_server(service_app, "service")

    # Tool is accessible in the main app with both prefixes
    tool = await main_app._tool_manager.get_tool("service_provider_compute")
    assert tool is not None


async def test_call_nested_imported_tool():
    """Test calling a tool through multiple levels of importing."""
    main_app = FastMCP("MainApp")
    service_app = FastMCP("ServiceApp")
    provider_app = FastMCP("ProviderApp")

    def calculate_value(input: int) -> int:
        return input * 2

    provider_app.add_tool(Tool.from_function(calculate_value, name="compute"))
    await service_app.import_server(provider_app, "provider")
    await main_app.import_server(service_app, "service")

    async with Client(main_app) as client:
        result = await client.call_tool("service_provider_compute", {"input": 21})
        assert result[0].text == "42"  # type: ignore[attr-defined]


async def test_import_with_proxy_tools():
    """
    Test importing with tools that have custom names (proxy tools).

    This tests that the tool's name doesn't change even though the registered
    name does, which is important because we need to forward that name to the
    proxy server correctly.
    """
    # Create apps
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    @api_app.tool
    def get_data(query: str) -> str:
        return f"Data for query: {query}"

    proxy_app = FastMCP.as_proxy(Client(api_app))
    await main_app.import_server(proxy_app, "api")

    async with Client(main_app) as client:
        result = await client.call_tool("api_get_data", {"query": "test"})
        assert result[0].text == "Data for query: test"  # type: ignore[attr-defined]


async def test_import_with_proxy_prompts():
    """
    Test importing with prompts that have custom keys.

    This tests that the prompt's name doesn't change even though the registered
    key does, which is important for correct rendering.
    """
    # Create apps
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    @api_app.prompt
    def greeting(name: str) -> str:
        """Example greeting prompt."""
        return f"Hello, {name} from API!"

    proxy_app = FastMCP.as_proxy(Client(api_app))
    await main_app.import_server(proxy_app, "api")

    async with Client(main_app) as client:
        result = await client.get_prompt("api_greeting", {"name": "World"})
        assert result.messages[0].content.text == "Hello, World from API!"  # type: ignore[attr-defined]
        assert result.description == "Example greeting prompt."


async def test_import_with_proxy_resources():
    """
    Test importing with resources that have custom keys.

    This tests that the resource's name doesn't change even though the registered
    key does, which is important for correct access.
    """
    # Create apps
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    # Create a resource in the API app
    @api_app.resource(uri="config://settings")
    def get_config():
        return {
            "api_key": "12345",
            "base_url": "https://api.example.com",
        }

    proxy_app = FastMCP.as_proxy(Client(api_app))
    await main_app.import_server(proxy_app, "api")

    # Access the resource through the main app with the prefixed key
    async with Client(main_app) as client:
        result = await client.read_resource("config://api/settings")
        content = json.loads(result[0].text)  # type: ignore[attr-defined]
        assert content["api_key"] == "12345"
        assert content["base_url"] == "https://api.example.com"


async def test_import_with_proxy_resource_templates():
    """
    Test importing with resource templates that have custom keys.

    This tests that the template's name doesn't change even though the registered
    key does, which is important for correct instantiation.
    """
    # Create apps
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    # Create a resource template in the API app
    @api_app.resource(uri="user://{name}/{email}")
    def create_user(name: str, email: str):
        return {"name": name, "email": email}

    proxy_app = FastMCP.as_proxy(Client(api_app))
    await main_app.import_server(proxy_app, "api")

    # Instantiate the template through the main app with the prefixed key

    quoted_name = quote("John Doe", safe="")
    quoted_email = quote("john@example.com", safe="")
    async with Client(main_app) as client:
        result = await client.read_resource(f"user://api/{quoted_name}/{quoted_email}")
        content = json.loads(result[0].text)  # type: ignore[attr-defined]
        assert content["name"] == "John Doe"
        assert content["email"] == "john@example.com"


async def test_import_invalid_resource_prefix():
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    # This test doesn't apply anymore with the new prefix format since we're not validating
    # the protocol://prefix/path format
    # Just import the server to maintain test coverage without deprecated parameters
    await main_app.import_server(api_app, "api")


async def test_import_invalid_resource_separator():
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    # This test is for maintaining coverage for importing with prefixes
    # We no longer pass the deprecated resource_separator parameter
    await main_app.import_server(api_app, "api")


async def test_import_with_no_prefix():
    """Test importing a server without providing a prefix."""
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    @sub_app.tool
    def sub_tool() -> str:
        return "Sub tool result"

    @sub_app.resource(uri="data://config")
    def sub_resource():
        return "Sub resource data"

    @sub_app.resource(uri="users://{user_id}/info")
    def sub_template(user_id: str):
        return f"Sub template for user {user_id}"

    @sub_app.prompt
    def sub_prompt() -> str:
        return "Sub prompt content"

    # Import without prefix
    await main_app.import_server(sub_app)

    # Verify all component types are accessible with original names
    assert "sub_tool" in main_app._tool_manager._tools
    assert "data://config" in main_app._resource_manager._resources
    assert "users://{user_id}/info" in main_app._resource_manager._templates
    assert "sub_prompt" in main_app._prompt_manager._prompts

    # Test actual functionality through Client
    async with Client(main_app) as client:
        # Test tool
        tool_result = await client.call_tool("sub_tool", {})
        assert tool_result[0].text == "Sub tool result"  # type: ignore[attr-defined]

        # Test resource
        resource_result = await client.read_resource("data://config")
        assert resource_result[0].text == "Sub resource data"  # type: ignore[attr-defined]

        # Test template
        template_result = await client.read_resource("users://123/info")
        assert template_result[0].text == "Sub template for user 123"  # type: ignore[attr-defined]

        # Test prompt
        prompt_result = await client.get_prompt("sub_prompt", {})
        assert prompt_result.messages is not None
        assert prompt_result.messages[0].content.text == "Sub prompt content"  # type: ignore[attr-defined]


async def test_import_conflict_resolution_tools():
    """Test that later imported tools overwrite earlier ones when names conflict."""
    main_app = FastMCP("MainApp")
    first_app = FastMCP("FirstApp")
    second_app = FastMCP("SecondApp")

    @first_app.tool(name="shared_tool")
    def first_shared_tool() -> str:
        return "First app tool"

    @second_app.tool(name="shared_tool")
    def second_shared_tool() -> str:
        return "Second app tool"

    # Import both apps without prefix
    await main_app.import_server(first_app)
    await main_app.import_server(second_app)

    async with Client(main_app) as client:
        # The later imported server should win
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert "shared_tool" in tool_names
        assert tool_names.count("shared_tool") == 1  # Should only appear once

        result = await client.call_tool("shared_tool", {})
        assert result[0].text == "Second app tool"  # type: ignore[attr-defined]


async def test_import_conflict_resolution_resources():
    """Test that later imported resources overwrite earlier ones when URIs conflict."""
    main_app = FastMCP("MainApp")
    first_app = FastMCP("FirstApp")
    second_app = FastMCP("SecondApp")

    @first_app.resource(uri="shared://data")
    def first_resource():
        return "First app data"

    @second_app.resource(uri="shared://data")
    def second_resource():
        return "Second app data"

    # Import both apps without prefix
    await main_app.import_server(first_app)
    await main_app.import_server(second_app)

    async with Client(main_app) as client:
        # The later imported server should win
        resources = await client.list_resources()
        resource_uris = [str(r.uri) for r in resources]
        assert "shared://data" in resource_uris
        assert resource_uris.count("shared://data") == 1  # Should only appear once

        result = await client.read_resource("shared://data")
        assert result[0].text == "Second app data"  # type: ignore[attr-defined]


async def test_import_conflict_resolution_templates():
    """Test that later imported templates overwrite earlier ones when URI templates conflict."""
    main_app = FastMCP("MainApp")
    first_app = FastMCP("FirstApp")
    second_app = FastMCP("SecondApp")

    @first_app.resource(uri="users://{user_id}/profile")
    def first_template(user_id: str):
        return f"First app user {user_id}"

    @second_app.resource(uri="users://{user_id}/profile")
    def second_template(user_id: str):
        return f"Second app user {user_id}"

    # Import both apps without prefix
    await main_app.import_server(first_app)
    await main_app.import_server(second_app)

    async with Client(main_app) as client:
        # The later imported server should win
        templates = await client.list_resource_templates()
        template_uris = [t.uriTemplate for t in templates]
        assert "users://{user_id}/profile" in template_uris
        assert (
            template_uris.count("users://{user_id}/profile") == 1
        )  # Should only appear once

        result = await client.read_resource("users://123/profile")
        assert result[0].text == "Second app user 123"  # type: ignore[attr-defined]


async def test_import_conflict_resolution_prompts():
    """Test that later imported prompts overwrite earlier ones when names conflict."""
    main_app = FastMCP("MainApp")
    first_app = FastMCP("FirstApp")
    second_app = FastMCP("SecondApp")

    @first_app.prompt(name="shared_prompt")
    def first_shared_prompt() -> str:
        return "First app prompt"

    @second_app.prompt(name="shared_prompt")
    def second_shared_prompt() -> str:
        return "Second app prompt"

    # Import both apps without prefix
    await main_app.import_server(first_app)
    await main_app.import_server(second_app)

    async with Client(main_app) as client:
        # The later imported server should win
        prompts = await client.list_prompts()
        prompt_names = [p.name for p in prompts]
        assert "shared_prompt" in prompt_names
        assert prompt_names.count("shared_prompt") == 1  # Should only appear once

        result = await client.get_prompt("shared_prompt", {})
        assert result.messages is not None
        assert result.messages[0].content.text == "Second app prompt"  # type: ignore[attr-defined]


async def test_import_conflict_resolution_with_prefix():
    """Test that later imported components overwrite earlier ones when prefixed names conflict."""
    main_app = FastMCP("MainApp")
    first_app = FastMCP("FirstApp")
    second_app = FastMCP("SecondApp")

    @first_app.tool(name="shared_tool")
    def first_shared_tool() -> str:
        return "First app tool"

    @second_app.tool(name="shared_tool")
    def second_shared_tool() -> str:
        return "Second app tool"

    # Import both apps with same prefix
    await main_app.import_server(first_app, "api")
    await main_app.import_server(second_app, "api")

    async with Client(main_app) as client:
        # The later imported server should win
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert "api_shared_tool" in tool_names
        assert tool_names.count("api_shared_tool") == 1  # Should only appear once

        result = await client.call_tool("api_shared_tool", {})
        assert result[0].text == "Second app tool"  # type: ignore[attr-defined]
