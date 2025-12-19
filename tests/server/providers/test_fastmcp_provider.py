"""Tests for FastMCPProvider."""

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.providers import FastMCPProvider


class TestToolOperations:
    """Test tool operations through FastMCPProvider."""

    async def test_list_tools(self):
        """Test listing tools from wrapped server."""
        server = FastMCP("Test")

        @server.tool
        def tool_one() -> str:
            return "one"

        @server.tool
        def tool_two() -> str:
            return "two"

        provider = FastMCPProvider(server)
        tools = await provider.list_tools()

        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"tool_one", "tool_two"}

    async def test_get_tool(self):
        """Test getting a specific tool by name."""
        server = FastMCP("Test")

        @server.tool
        def my_tool() -> str:
            return "result"

        provider = FastMCPProvider(server)
        tool = await provider.get_tool("my_tool")

        assert tool is not None
        assert tool.name == "my_tool"

    async def test_get_nonexistent_tool_returns_none(self):
        """Test that getting a nonexistent tool returns None."""
        server = FastMCP("Test")
        provider = FastMCPProvider(server)

        tool = await provider.get_tool("nonexistent")
        assert tool is None

    async def test_call_tool_via_client(self):
        """Test calling a tool through a server using the provider."""
        sub = FastMCP("Sub")

        @sub.tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        main = FastMCP("Main")
        main._providers.append(FastMCPProvider(sub))

        async with Client(main) as client:
            result = await client.call_tool("greet", {"name": "World"})
            assert result.data == "Hello, World!"


class TestResourceOperations:
    """Test resource operations through FastMCPProvider."""

    async def test_list_resources(self):
        """Test listing resources from wrapped server."""
        server = FastMCP("Test")

        @server.resource("resource://one")
        def resource_one() -> str:
            return "one"

        @server.resource("resource://two")
        def resource_two() -> str:
            return "two"

        provider = FastMCPProvider(server)
        resources = await provider.list_resources()

        assert len(resources) == 2
        uris = {str(r.uri) for r in resources}
        assert uris == {"resource://one", "resource://two"}

    async def test_get_resource(self):
        """Test getting a specific resource by URI."""
        server = FastMCP("Test")

        @server.resource("resource://data")
        def my_resource() -> str:
            return "content"

        provider = FastMCPProvider(server)
        resource = await provider.get_resource("resource://data")

        assert resource is not None
        assert str(resource.uri) == "resource://data"

    async def test_read_resource_via_client(self):
        """Test reading a resource through a server using the provider."""
        sub = FastMCP("Sub")

        @sub.resource("resource://data")
        def my_resource() -> str:
            return "content"

        main = FastMCP("Main")
        main._providers.append(FastMCPProvider(sub))

        async with Client(main) as client:
            result = await client.read_resource("resource://data")
            assert result[0].text == "content"  # type: ignore[attr-defined]


class TestResourceTemplateOperations:
    """Test resource template operations through FastMCPProvider."""

    async def test_list_resource_templates(self):
        """Test listing resource templates from wrapped server."""
        server = FastMCP("Test")

        @server.resource("resource://{id}/data")
        def my_template(id: str) -> str:
            return f"data for {id}"

        provider = FastMCPProvider(server)
        templates = await provider.list_resource_templates()

        assert len(templates) == 1
        assert templates[0].uri_template == "resource://{id}/data"

    async def test_get_resource_template(self):
        """Test getting a template that matches a URI."""
        server = FastMCP("Test")

        @server.resource("resource://{id}/data")
        def my_template(id: str) -> str:
            return f"data for {id}"

        provider = FastMCPProvider(server)
        template = await provider.get_resource_template("resource://123/data")

        assert template is not None

    async def test_read_resource_template_via_client(self):
        """Test reading a resource via template through a server using the provider."""
        sub = FastMCP("Sub")

        @sub.resource("resource://{id}/data")
        def my_template(id: str) -> str:
            return f"data for {id}"

        main = FastMCP("Main")
        main._providers.append(FastMCPProvider(sub))

        async with Client(main) as client:
            result = await client.read_resource("resource://123/data")
            assert result[0].text == "data for 123"  # type: ignore[attr-defined]


class TestPromptOperations:
    """Test prompt operations through FastMCPProvider."""

    async def test_list_prompts(self):
        """Test listing prompts from wrapped server."""
        server = FastMCP("Test")

        @server.prompt
        def prompt_one() -> str:
            return "one"

        @server.prompt
        def prompt_two() -> str:
            return "two"

        provider = FastMCPProvider(server)
        prompts = await provider.list_prompts()

        assert len(prompts) == 2
        names = {p.name for p in prompts}
        assert names == {"prompt_one", "prompt_two"}

    async def test_get_prompt(self):
        """Test getting a specific prompt by name."""
        server = FastMCP("Test")

        @server.prompt
        def my_prompt() -> str:
            return "content"

        provider = FastMCPProvider(server)
        prompt = await provider.get_prompt("my_prompt")

        assert prompt is not None
        assert prompt.name == "my_prompt"

    async def test_render_prompt_via_client(self):
        """Test rendering a prompt through a server using the provider."""
        sub = FastMCP("Sub")

        @sub.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        main = FastMCP("Main")
        main._providers.append(FastMCPProvider(sub))

        async with Client(main) as client:
            result = await client.get_prompt("greet", {"name": "World"})
            assert result.messages[0].content.text == "Hello, World!"  # type: ignore[attr-defined]


class TestServerReference:
    """Test that provider maintains reference to wrapped server."""

    def test_server_attribute(self):
        """Test that provider exposes the wrapped server."""
        server = FastMCP("Test")
        provider = FastMCPProvider(server)

        assert provider.server is server

    def test_server_name_accessible(self):
        """Test that server name is accessible through provider."""
        server = FastMCP("MyServer")
        provider = FastMCPProvider(server)

        assert provider.server.name == "MyServer"
