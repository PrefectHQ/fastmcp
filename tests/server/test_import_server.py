import json
from urllib.parse import quote

from fastmcp.client.client import Client
from fastmcp.server.server import FastMCP


async def test_import_basic_functionality():
    """Test that the import method properly imports tools and other resources."""
    # Create main app and sub-app
    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    # Add a tool to the sub-app
    @sub_app.tool()
    def sub_tool() -> str:
        return "This is from the sub app"

    # Import the sub-app to the main app
    await main_app.import_server("sub", sub_app)

    # Verify the tool was imported with the prefix
    assert "sub_sub_tool" in main_app._tool_manager._tools
    assert "sub_tool" in sub_app._tool_manager._tools

    # Verify the original tool still exists in the sub-app
    tool = main_app._tool_manager.get_tool("sub_sub_tool")
    assert tool is not None
    assert tool.name == "sub_tool"
    assert callable(tool.fn)


async def test_import_multiple_apps():
    """Test importing multiple apps to a main app."""
    # Create main app and multiple sub-apps
    main_app = FastMCP("MainApp")
    weather_app = FastMCP("WeatherApp")
    news_app = FastMCP("NewsApp")

    # Add tools to each sub-app
    @weather_app.tool()
    def get_forecast() -> str:
        return "Weather forecast"

    @news_app.tool()
    def get_headlines() -> str:
        return "News headlines"

    # Import both sub-apps to the main app
    await main_app.import_server("weather", weather_app)
    await main_app.import_server("news", news_app)

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
    @first_app.tool()
    def first_tool() -> str:
        return "First app tool"

    @second_app.tool()
    def second_tool() -> str:
        return "Second app tool"

    # Import first app
    await main_app.import_server("api", first_app)
    assert "api_first_tool" in main_app._tool_manager._tools

    # Import second app to same prefix
    await main_app.import_server("api", second_app)

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
    await main_app.import_server("data", data_app)

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
    await main_app.import_server("api", user_app)

    # Verify the template was imported with the prefix
    assert "users://api/{user_id}/profile" in main_app._resource_manager._templates


async def test_import_with_prompts():
    """Test importing with prompts."""
    # Create apps
    main_app = FastMCP("MainApp")
    assistant_app = FastMCP("AssistantApp")

    # Add a prompt to the assistant app
    @assistant_app.prompt()
    def greeting(name: str) -> str:
        return f"Hello, {name}!"

    # Import the assistant app
    await main_app.import_server("assistant", assistant_app)

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
    await main_app.import_server("data", weather_app)
    await main_app.import_server("content", news_app)

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
    @python_app.prompt()
    def review_python(code: str) -> str:
        return f"Reviewing Python code:\n{code}"

    @sql_app.prompt()
    def explain_sql(query: str) -> str:
        return f"Explaining SQL query:\n{query}"

    # Import both apps
    await main_app.import_server("python", python_app)
    await main_app.import_server("sql", sql_app)

    # Verify prompts were imported with correct prefixes
    assert "python_review_python" in main_app._prompt_manager._prompts
    assert "sql_explain_sql" in main_app._prompt_manager._prompts


async def test_tool_custom_name_preserved_when_imported():
    """Test that a tool's custom name is preserved when imported."""
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    def fetch_data(query: str) -> str:
        return f"Data for query: {query}"

    api_app.add_tool(fetch_data, name="get_data")
    await main_app.import_server("api", api_app)

    # Check that the tool is accessible by its prefixed name
    tool = main_app._tool_manager.get_tool("api_get_data")
    assert tool is not None

    # Check that the function name is preserved
    assert tool.fn.__name__ == "fetch_data"


async def test_call_imported_custom_named_tool():
    """Test calling an imported tool with a custom name."""
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    def fetch_data(query: str) -> str:
        return f"Data for query: {query}"

    api_app.add_tool(fetch_data, name="get_data")
    await main_app.import_server("api", api_app)

    async with Client(main_app) as client:
        result = await client.call_tool("api_get_data", {"query": "test"})
        assert result[0].text == "Data for query: test"  # type: ignore[attr-defined]


async def test_first_level_importing_with_custom_name():
    """Test that a tool with a custom name is correctly imported at the first level."""
    service_app = FastMCP("ServiceApp")
    provider_app = FastMCP("ProviderApp")

    def calculate_value(input: int) -> int:
        return input * 2

    provider_app.add_tool(calculate_value, name="compute")
    await service_app.import_server("provider", provider_app)

    # Tool is accessible in the service app with the first prefix
    tool = service_app._tool_manager.get_tool("provider_compute")
    assert tool is not None
    assert tool.fn.__name__ == "calculate_value"


async def test_nested_importing_preserves_prefixes():
    """Test that importing a previously imported app preserves prefixes."""
    main_app = FastMCP("MainApp")
    service_app = FastMCP("ServiceApp")
    provider_app = FastMCP("ProviderApp")

    def calculate_value(input: int) -> int:
        return input * 2

    provider_app.add_tool(calculate_value, name="compute")
    await service_app.import_server("provider", provider_app)
    await main_app.import_server("service", service_app)

    # Tool is accessible in the main app with both prefixes
    tool = main_app._tool_manager.get_tool("service_provider_compute")
    assert tool is not None


async def test_call_nested_imported_tool():
    """Test calling a tool through multiple levels of importing."""
    main_app = FastMCP("MainApp")
    service_app = FastMCP("ServiceApp")
    provider_app = FastMCP("ProviderApp")

    def calculate_value(input: int) -> int:
        return input * 2

    provider_app.add_tool(calculate_value, name="compute")
    await service_app.import_server("provider", provider_app)
    await main_app.import_server("service", service_app)

    result = await main_app._tool_manager.call_tool(
        "service_provider_compute", {"input": 21}
    )
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

    @api_app.tool()
    def get_data(query: str) -> str:
        return f"Data for query: {query}"

    proxy_app = FastMCP.as_proxy(Client(api_app))
    await main_app.import_server("api", proxy_app)

    result = await main_app._mcp_call_tool("api_get_data", {"query": "test"})
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

    @api_app.prompt()
    def greeting(name: str) -> str:
        """Example greeting prompt."""
        return f"Hello, {name} from API!"

    proxy_app = FastMCP.as_proxy(Client(api_app))
    await main_app.import_server("api", proxy_app)

    result = await main_app._mcp_get_prompt("api_greeting", {"name": "World"})
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
    await main_app.import_server("api", proxy_app)

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
    await main_app.import_server("api", proxy_app)

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
    await main_app.import_server("api_sub", api_app)


async def test_import_invalid_resource_separator():
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    # This test is for maintaining coverage for importing with prefixes
    # We no longer pass the deprecated resource_separator parameter
    await main_app.import_server("api", api_app)
