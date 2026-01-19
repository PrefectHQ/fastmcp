import json
from urllib.parse import quote

from mcp.types import TextContent, TextResourceContents

from fastmcp.client.client import Client
from fastmcp.server.server import FastMCP
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.tools.tool import Tool
from tests.conftest import get_fn_name


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
    main_tools = await main_app.list_tools()
    sub_tools = await sub_app.list_tools()
    assert any(t.name == "sub_sub_tool" for t in main_tools)
    assert any(t.name == "sub_tool" for t in sub_tools)

    # Verify the original tool still exists in the sub-app
    tool = await main_app.get_tool("sub_sub_tool")
    assert tool is not None
    # import_server creates copies with prefixed names (unlike mount which proxies)
    assert tool.name == "sub_sub_tool"
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
    tools = await main_app.list_tools()
    assert any(t.name == "weather_get_forecast" for t in tools)
    assert any(t.name == "news_get_headlines" for t in tools)


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
    tools = await main_app.list_tools()
    assert any(t.name == "api_first_tool" for t in tools)

    # Import second app to same prefix
    await main_app.import_server(second_app, "api")

    # Verify second tool is there
    tools = await main_app.list_tools()
    assert any(t.name == "api_second_tool" for t in tools)

    # Tools from both imports are combined
    assert any(t.name == "api_first_tool" for t in tools)


async def test_import_with_resources():
    """Test importing with resources."""
    # Create apps
    main_app = FastMCP("MainApp")
    data_app = FastMCP("DataApp")

    # Add a resource to the data app
    @data_app.resource(uri="data://users")
    async def get_users() -> str:
        return "user1, user2"

    # Import the data app
    await main_app.import_server(data_app, "data")

    # Verify the resource was imported with the prefix
    resources = await main_app.list_resources()
    assert any(str(r.uri) == "data://data/users" for r in resources)


async def test_import_with_resource_templates():
    """Test importing with resource templates."""
    # Create apps
    main_app = FastMCP("MainApp")
    user_app = FastMCP("UserApp")

    # Add a resource template to the user app
    @user_app.resource(uri="users://{user_id}/profile")
    def get_user_profile(user_id: str) -> str:
        import json

        return json.dumps(
            {"id": user_id, "name": f"User {user_id}"}, separators=(",", ":")
        )

    # Import the user app
    await main_app.import_server(user_app, "api")

    # Verify the template was imported with the prefix
    templates = await main_app.list_resource_templates()
    assert any(t.uri_template == "users://api/{user_id}/profile" for t in templates)


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
    prompts = await main_app.list_prompts()
    assert any(p.name == "assistant_greeting" for p in prompts)


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
    templates = await main_app.list_resource_templates()
    assert any(t.uri_template == "weather://data/{city}" for t in templates)
    assert any(t.uri_template == "news://content/{category}" for t in templates)


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
    prompts = await main_app.list_prompts()
    assert any(p.name == "python_review_python" for p in prompts)
    assert any(p.name == "sql_explain_sql" for p in prompts)


async def test_tool_custom_name_preserved_when_imported():
    """Test that a tool's custom name is preserved when imported."""
    main_app = FastMCP("MainApp")
    api_app = FastMCP("APIApp")

    def fetch_data(query: str) -> str:
        return f"Data for query: {query}"

    api_app.add_tool(Tool.from_function(fetch_data, name="get_data"))
    await main_app.import_server(api_app, "api")

    # Check that the tool is accessible by its prefixed name
    tool = await main_app.get_tool("api_get_data")
    assert tool is not None

    # Check that the function name is preserved
    assert isinstance(tool, FunctionTool)
    assert get_fn_name(tool.fn) == "fetch_data"


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
        assert result.data == "Data for query: test"


async def test_first_level_importing_with_custom_name():
    """Test that a tool with a custom name is correctly imported at the first level."""
    service_app = FastMCP("ServiceApp")
    provider_app = FastMCP("ProviderApp")

    def calculate_value(input: int) -> int:
        return input * 2

    provider_app.add_tool(Tool.from_function(calculate_value, name="compute"))
    await service_app.import_server(provider_app, "provider")

    # Tool is accessible in the service app with the first prefix
    tool = await service_app.get_tool("provider_compute")
    assert tool is not None
    assert isinstance(tool, FunctionTool)
    assert get_fn_name(tool.fn) == "calculate_value"


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
    tool = await main_app.get_tool("service_provider_compute")
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
        assert result.data == 42


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

    proxy_app = FastMCP.as_proxy(api_app)
    await main_app.import_server(proxy_app, "api")

    async with Client(main_app) as client:
        result = await client.call_tool("api_get_data", {"query": "test"})
        assert result.data == "Data for query: test"


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

    proxy_app = FastMCP.as_proxy(api_app)
    await main_app.import_server(proxy_app, "api")

    async with Client(main_app) as client:
        result = await client.get_prompt("api_greeting", {"name": "World"})
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Hello, World from API!"
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
    def get_config() -> str:
        import json

        return json.dumps(
            {
                "api_key": "12345",
                "base_url": "https://api.example.com",
            }
        )

    proxy_app = FastMCP.as_proxy(api_app)
    await main_app.import_server(proxy_app, "api")

    # Access the resource through the main app with the prefixed key
    async with Client(main_app) as client:
        result = await client.read_resource("config://api/settings")
        assert isinstance(result[0], TextResourceContents)
        content = json.loads(result[0].text)
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
    def create_user(name: str, email: str) -> str:
        import json

        return json.dumps({"name": name, "email": email})

    proxy_app = FastMCP.as_proxy(api_app)
    await main_app.import_server(proxy_app, "api")

    # Instantiate the template through the main app with the prefixed key

    quoted_name = quote("John Doe", safe="")
    quoted_email = quote("john@example.com", safe="")
    async with Client(main_app) as client:
        result = await client.read_resource(f"user://api/{quoted_name}/{quoted_email}")
        assert isinstance(result[0], TextResourceContents)
        content = json.loads(result[0].text)
        assert content["name"] == "John Doe"
        assert content["email"] == "john@example.com"


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
    tools = await main_app.list_tools()
    resources = await main_app.list_resources()
    templates = await main_app.list_resource_templates()
    prompts = await main_app.list_prompts()
    assert any(t.name == "sub_tool" for t in tools)
    assert any(str(r.uri) == "data://config" for r in resources)
    assert any(t.uri_template == "users://{user_id}/info" for t in templates)
    assert any(p.name == "sub_prompt" for p in prompts)

    # Test actual functionality through Client
    async with Client(main_app) as client:
        # Test tool
        tool_result = await client.call_tool("sub_tool", {})
        assert tool_result.data == "Sub tool result"

        # Test resource
        resource_result = await client.read_resource("data://config")
        assert isinstance(resource_result[0], TextResourceContents)
        assert resource_result[0].text == "Sub resource data"

        # Test template
        template_result = await client.read_resource("users://123/info")
        assert isinstance(template_result[0], TextResourceContents)
        assert template_result[0].text == "Sub template for user 123"

        # Test prompt
        prompt_result = await client.get_prompt("sub_prompt", {})
        assert prompt_result.messages is not None
        assert isinstance(prompt_result.messages[0].content, TextContent)
        assert prompt_result.messages[0].content.text == "Sub prompt content"


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
        assert result.data == "Second app tool"


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
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Second app data"


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
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Second app user 123"


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
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Second app prompt"


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
        assert result.data == "Second app tool"


async def test_import_server_resource_uri_prefixing():
    """Test that resource URIs are prefixed when using import_server (names are NOT prefixed)."""
    # Create a sub-server with a resource
    sub_server = FastMCP("SubServer")

    @sub_server.resource("resource://test_resource")
    def test_resource() -> str:
        return "Test content"

    # Create main server and import sub-server with prefix
    main_server = FastMCP("MainServer")
    await main_server.import_server(sub_server, prefix="imported")

    # Get resources and verify URI prefixing (name should NOT be prefixed)
    resources = await main_server.list_resources()
    resource = next(
        r for r in resources if str(r.uri) == "resource://imported/test_resource"
    )
    assert resource.name == "test_resource"


async def test_import_server_resource_template_uri_prefixing():
    """Test that resource template URIs are prefixed when using import_server (names are NOT prefixed)."""
    # Create a sub-server with a resource template
    sub_server = FastMCP("SubServer")

    @sub_server.resource("resource://data/{item_id}")
    def data_template(item_id: str) -> str:
        return f"Data for {item_id}"

    # Create main server and import sub-server with prefix
    main_server = FastMCP("MainServer")
    await main_server.import_server(sub_server, prefix="imported")

    # Get resource templates and verify URI prefixing (name should NOT be prefixed)
    templates = await main_server.list_resource_templates()
    template = next(
        t for t in templates if t.uri_template == "resource://imported/data/{item_id}"
    )
    assert template.name == "data_template"


async def test_import_server_with_new_prefix_format():
    """Test that import_server correctly uses the new prefix format."""
    # Create a server with resources
    source_server = FastMCP(name="SourceServer")

    @source_server.resource("resource://test-resource")
    def get_resource():
        return "Resource content"

    @source_server.resource("resource:///absolute/path")
    def get_absolute_resource():
        return "Absolute resource content"

    @source_server.resource("resource://{param}/template")
    def get_template_resource(param: str):
        return f"Template resource with {param}"

    # Create target server and import the source server
    target_server = FastMCP(name="TargetServer")
    await target_server.import_server(source_server, "imported")

    # Check that the resources were imported with the correct prefixes
    resources = await target_server.list_resources()
    templates = await target_server.list_resource_templates()

    assert any(str(r.uri) == "resource://imported/test-resource" for r in resources)
    assert any(str(r.uri) == "resource://imported//absolute/path" for r in resources)
    assert any(
        t.uri_template == "resource://imported/{param}/template" for t in templates
    )

    # Verify we can access the resources
    async with Client(target_server) as client:
        result = await client.read_resource("resource://imported/test-resource")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Resource content"

        result = await client.read_resource("resource://imported//absolute/path")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Absolute resource content"

        result = await client.read_resource("resource://imported/param-value/template")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Template resource with param-value"
