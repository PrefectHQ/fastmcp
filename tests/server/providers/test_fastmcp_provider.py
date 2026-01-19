"""Tests for FastMCPProvider."""

import mcp.types as mt

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.prompts.prompt import PromptResult
from fastmcp.resources.resource import ResourceResult
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers import FastMCPProvider
from fastmcp.tools.tool import ToolResult


class ToolTracingMiddleware(Middleware):
    """Middleware that traces tool calls."""

    def __init__(self, name: str, calls: list[str]):
        super().__init__()
        self._name = name
        self._calls = calls

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        self._calls.append(f"{self._name}:before")
        result = await call_next(context)
        self._calls.append(f"{self._name}:after")
        return result


class ResourceTracingMiddleware(Middleware):
    """Middleware that traces resource reads."""

    def __init__(self, name: str, calls: list[str]):
        super().__init__()
        self._name = name
        self._calls = calls

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        self._calls.append(f"{self._name}:before")
        result = await call_next(context)
        self._calls.append(f"{self._name}:after")
        return result


class PromptTracingMiddleware(Middleware):
    """Middleware that traces prompt gets."""

    def __init__(self, name: str, calls: list[str]):
        super().__init__()
        self._name = name
        self._calls = calls

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        self._calls.append(f"{self._name}:before")
        result = await call_next(context)
        self._calls.append(f"{self._name}:after")
        return result


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
        main.add_provider(FastMCPProvider(sub))

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
        main.add_provider(FastMCPProvider(sub))

        async with Client(main) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], mt.TextResourceContents)
            assert result[0].text == "content"


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
        main.add_provider(FastMCPProvider(sub))

        async with Client(main) as client:
            result = await client.read_resource("resource://123/data")
            assert isinstance(result[0], mt.TextResourceContents)
            assert result[0].text == "data for 123"


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
        main.add_provider(FastMCPProvider(sub))

        async with Client(main) as client:
            result = await client.get_prompt("greet", {"name": "World"})
            assert isinstance(result.messages[0].content, mt.TextContent)
            assert result.messages[0].content.text == "Hello, World!"


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


class TestMiddlewareChain:
    """Test that middleware runs at each level of mounted servers."""

    async def test_tool_middleware_three_levels(self):
        """Middleware runs at parent, child, and grandchild levels for tools."""
        calls: list[str] = []

        grandchild = FastMCP("Grandchild")

        @grandchild.tool
        async def compute(x: int) -> int:
            calls.append("grandchild:tool")
            return x * 2

        grandchild.add_middleware(ToolTracingMiddleware("grandchild", calls))

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")
        child.add_middleware(ToolTracingMiddleware("child", calls))

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")
        parent.add_middleware(ToolTracingMiddleware("parent", calls))

        async with Client(parent) as client:
            result = await client.call_tool("c_gc_compute", {"x": 5})
            assert result.data == 10

        assert calls == [
            "parent:before",
            "child:before",
            "grandchild:before",
            "grandchild:tool",
            "grandchild:after",
            "child:after",
            "parent:after",
        ]

    async def test_resource_middleware_three_levels(self):
        """Middleware runs at parent, child, and grandchild levels for resources."""
        calls: list[str] = []

        grandchild = FastMCP("Grandchild")

        @grandchild.resource("data://value")
        async def get_data() -> str:
            calls.append("grandchild:resource")
            return "result"

        grandchild.add_middleware(ResourceTracingMiddleware("grandchild", calls))

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")
        child.add_middleware(ResourceTracingMiddleware("child", calls))

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")
        parent.add_middleware(ResourceTracingMiddleware("parent", calls))

        async with Client(parent) as client:
            result = await client.read_resource("data://c/gc/value")
            assert isinstance(result[0], mt.TextResourceContents)
            assert result[0].text == "result"

        assert calls == [
            "parent:before",
            "child:before",
            "grandchild:before",
            "grandchild:resource",
            "grandchild:after",
            "child:after",
            "parent:after",
        ]

    async def test_prompt_middleware_three_levels(self):
        """Middleware runs at parent, child, and grandchild levels for prompts."""
        calls: list[str] = []

        grandchild = FastMCP("Grandchild")

        @grandchild.prompt
        async def greet(name: str) -> str:
            calls.append("grandchild:prompt")
            return f"Hello, {name}!"

        grandchild.add_middleware(PromptTracingMiddleware("grandchild", calls))

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")
        child.add_middleware(PromptTracingMiddleware("child", calls))

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")
        parent.add_middleware(PromptTracingMiddleware("parent", calls))

        async with Client(parent) as client:
            result = await client.get_prompt("c_gc_greet", {"name": "World"})
            assert isinstance(result.messages[0].content, mt.TextContent)
            assert result.messages[0].content.text == "Hello, World!"

        assert calls == [
            "parent:before",
            "child:before",
            "grandchild:before",
            "grandchild:prompt",
            "grandchild:after",
            "child:after",
            "parent:after",
        ]

    async def test_resource_template_middleware_three_levels(self):
        """Middleware runs at all levels for resource templates."""
        calls: list[str] = []

        grandchild = FastMCP("Grandchild")

        @grandchild.resource("item://{id}")
        async def get_item(id: str) -> str:
            calls.append("grandchild:template")
            return f"item-{id}"

        grandchild.add_middleware(ResourceTracingMiddleware("grandchild", calls))

        child = FastMCP("Child")
        child.mount(grandchild, namespace="gc")
        child.add_middleware(ResourceTracingMiddleware("child", calls))

        parent = FastMCP("Parent")
        parent.mount(child, namespace="c")
        parent.add_middleware(ResourceTracingMiddleware("parent", calls))

        async with Client(parent) as client:
            result = await client.read_resource("item://c/gc/42")
            assert isinstance(result[0], mt.TextResourceContents)
            assert result[0].text == "item-42"

        assert calls == [
            "parent:before",
            "child:before",
            "grandchild:before",
            "grandchild:template",
            "grandchild:after",
            "child:after",
            "parent:after",
        ]
