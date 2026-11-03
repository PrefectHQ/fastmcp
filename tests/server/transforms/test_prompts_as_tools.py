"""Tests for PromptsAsTools transform."""

import json

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.context import Context
from fastmcp.server.transforms import PromptsAsTools, VersionFilter
from fastmcp.server.transforms.search import RegexSearchTransform


class TestPromptsAsToolsBasic:
    """Test basic PromptsAsTools functionality."""

    async def test_adds_list_prompts_tool(self):
        """Transform adds list_prompts tool."""
        mcp = FastMCP("Test")
        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "list_prompts" in tool_names

    async def test_adds_get_prompt_tool(self):
        """Transform adds get_prompt tool."""
        mcp = FastMCP("Test")
        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "get_prompt" in tool_names

    async def test_preserves_existing_tools(self):
        """Transform preserves existing tools."""
        mcp = FastMCP("Test")

        @mcp.tool
        def my_tool() -> str:
            return "result"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "my_tool" in tool_names
            assert "list_prompts" in tool_names
            assert "get_prompt" in tool_names

    async def test_does_not_override_user_defined_tools(self):
        """User-defined tools named list_prompts or get_prompt take precedence."""
        mcp = FastMCP("Test")

        @mcp.tool
        def list_prompts() -> str:
            return "user list_prompts"

        @mcp.tool
        def get_prompt(name: str) -> str:
            return f"user get_prompt: {name}"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert tool_names.count("list_prompts") == 1
            assert tool_names.count("get_prompt") == 1

            result1 = await client.call_tool("list_prompts", {})
            assert result1.data == "user list_prompts"

            result2 = await client.call_tool("get_prompt", {"name": "test_prompt"})
            assert result2.data == "user get_prompt: test_prompt"

    async def test_versioned_user_tool_with_version_filter(self):
        """Synthetic tools remain discoverable when versioned user tool is filtered out."""
        mcp = FastMCP("Test")

        @mcp.tool(version="2.0")
        def get_prompt(name: str) -> str:
            return f"user v2: {name}"

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(PromptsAsTools(mcp))
        mcp.add_transform(VersionFilter(version_lt="2.0"))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            result = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(result.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_regex_search_transform_preceding_prompts_as_tools(self):
        """When RegexSearchTransform precedes PromptsAsTools, synthetic tool is advertised and called."""
        mcp = FastMCP("Test")

        @mcp.tool
        def get_prompt(query: str) -> str:
            return f"user: {query}"

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(RegexSearchTransform())
        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names
            assert "list_prompts" in tool_names

            # Calling get_prompt directly calls the advertised synthetic tool
            result = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(result.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_provider_scoped_search_transform_hiding(self):
        """When child provider has RegexSearchTransform, parent PromptsAsTools advertises and calls synthetic tool."""
        from fastmcp.server.providers import LocalProvider

        mcp = FastMCP("Test")
        child = LocalProvider()

        @child.tool
        def get_prompt(query: str) -> str:
            return f"child: {query}"

        child.add_transform(RegexSearchTransform())
        mcp.add_provider(child)

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names
            assert "list_prompts" in tool_names

            result = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(result.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_auth_denied_user_tool_falls_back_to_synthetic_tool(self):
        """When user tool is denied by auth, synthetic tool is advertised and callable."""
        from fastmcp.server.auth import AuthContext
        from fastmcp.server.context import _current_transport

        def _deny(ctx: AuthContext) -> bool:
            return False

        mcp = FastMCP("Test")

        @mcp.tool(auth=_deny)
        def get_prompt(query: str) -> str:
            return f"user: {query}"

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(PromptsAsTools(mcp))

        token = _current_transport.set("http")
        try:
            tools = await mcp.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            tool = await mcp.get_tool("get_prompt")
            assert tool is not None
            assert "name" in tool.parameters["properties"]
        finally:
            _current_transport.reset(token)

    async def test_visible_colliding_parent_tool_with_unrelated_search_child(
        self,
    ):
        """Visible colliding parent user tool is preferred over synthetic tool even if an unrelated child has search transform."""
        from fastmcp.server.providers import LocalProvider

        mcp = FastMCP("Test")

        @mcp.tool
        def get_prompt(query: str) -> str:
            return f"parent: {query}"

        child = LocalProvider()

        @child.tool
        def unrelated_tool() -> str:
            return "unrelated"

        child.add_transform(RegexSearchTransform())
        mcp.add_provider(child)

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            result = await client.call_tool("get_prompt", {"query": "hello"})
            assert result.data == "parent: hello"

    async def test_disabled_user_tool_falls_back_to_synthetic_tool(self):
        """When user tool is disabled, synthetic tool is advertised and callable."""
        mcp = FastMCP("Test")

        @mcp.tool(tags={"internal"})
        def get_prompt(query: str) -> str:
            return f"user: {query}"

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.disable(tags={"internal"})
        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            result = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(result.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_mounted_child_search_hides_user_tool(self):
        """When an unnamespaced mounted child defines get_prompt behind search transform, parent uses synthetic tool."""
        parent = FastMCP("Parent")
        child = FastMCP("Child")

        @child.tool
        def get_prompt(query: str) -> str:
            return f"child: {query}"

        child.add_transform(RegexSearchTransform())
        parent.mount(child)

        @parent.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        parent.add_transform(PromptsAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            result = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(result.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_code_mode_preceding_prompts_as_tools(self):
        """When CodeMode precedes PromptsAsTools, hidden user tool does not prevent synthetic tool."""
        from fastmcp.experimental.transforms.code_mode import CodeMode

        mcp = FastMCP("Test")

        @mcp.tool
        def get_prompt(query: str) -> str:
            return f"user: {query}"

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(CodeMode())
        mcp.add_transform(PromptsAsTools(mcp))

        tool = await mcp.get_tool("get_prompt")
        assert tool is not None
        assert "name" in tool.parameters["properties"]

    async def test_retaining_catalog_transform_preserves_colliding_user_tool(self):
        """A pass-through CatalogTransform leaves user tool advertised and callable via Client."""
        from fastmcp.server.transforms.catalog import CatalogTransform

        class RetainingTransform(CatalogTransform):
            async def transform_tools(self, tools):
                return tools

        mcp = FastMCP("Test")

        @mcp.tool
        def get_prompt(query: str) -> str:
            return f"user: {query}"

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(RetainingTransform())
        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "get_prompt")
            assert "query" in tool.input_schema["properties"]

            result = await client.call_tool("get_prompt", {"query": "hello"})
            assert result.data == "user: hello"

    async def test_subsequent_disable_transform_leaves_synthetic_tool_accessible(self):
        """When mcp.disable follows PromptsAsTools, disabled user tool does not suppress synthetic tool."""
        mcp = FastMCP("Test")

        @mcp.tool(tags={"internal"})
        def get_prompt(query: str) -> str:
            return f"internal: {query}"

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(PromptsAsTools(mcp))
        mcp.disable(tags={"internal"})

        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            result = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(result.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_type_aware_identity_does_not_hijack_child_tool_ownership(self):
        """Parent prompt named get_prompt does not hijack provider identity of child's search-hidden tool."""
        parent = FastMCP("Parent")
        child = FastMCP("Child")

        @child.tool
        def get_prompt(query: str) -> str:
            return f"child: {query}"

        child.add_transform(RegexSearchTransform())
        parent.mount(child)

        @parent.prompt(name="get_prompt")
        def parent_prompt(name: str = "World") -> str:
            return f"Hello, {name}!"

        parent.add_transform(PromptsAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            result = await client.call_tool(
                "get_prompt", {"name": "get_prompt", "arguments": {"name": "Alice"}}
            )
            data = json.loads(result.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_custom_provider_with_search_transform_hides_user_tool(self):
        """Providers resolving via _get_tool with SearchTransform yield synthetic prompt tool."""
        from fastmcp.server.providers.base import Provider
        from fastmcp.tools.base import Tool

        class CustomProvider(Provider):
            def __init__(self):
                super().__init__()
                self._tool = Tool.from_function(
                    lambda query: f"custom: {query}", name="get_prompt"
                )

            async def _list_tools(self):
                return [self._tool]

            async def _get_tool(self, name: str, version=None):
                return self._tool if name == "get_prompt" else None

        provider = CustomProvider()
        provider.add_transform(RegexSearchTransform())

        server = FastMCP("Server", providers=[provider])

        @server.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        server.add_transform(PromptsAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            result = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(result.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_session_disabled_user_tool_yields_synthetic_tool(self):
        """When a user tool colliding with synthetic tool is disabled in session, synthetic tool is advertised and callable."""
        mcp = FastMCP("Server")

        @mcp.tool(tags={"internal"})
        def get_prompt(query: str) -> str:
            return f"user: {query}"

        @mcp.tool
        async def deactivate_internal(ctx: Context) -> str:
            await ctx.disable_components(tags={"internal"})
            return "deactivated"

        @mcp.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            tools = await client.list_tools()
            user_tool = next(t for t in tools if t.name == "get_prompt")
            assert "query" in user_tool.input_schema.get("properties", {})

            await client.call_tool("deactivate_internal", {})

            tools_after = await client.list_tools()
            syn_tool = next((t for t in tools_after if t.name == "get_prompt"), None)
            assert syn_tool is not None
            assert "arguments" in syn_tool.input_schema.get("properties", {})

            res = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(res.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_version_spec_passed_to_provider_get_tool(self):
        """Provider expecting VersionSpec in _get_tool receives valid VersionSpec when probed."""
        from typing import Any

        from fastmcp.server.providers.base import Provider
        from fastmcp.tools.base import Tool
        from fastmcp.utilities.versions import VersionSpec

        received_spec: list[Any] = []

        class VersionAwareProvider(Provider):
            def __init__(self):
                super().__init__()
                self._tool = Tool.from_function(
                    lambda query: f"custom: {query}",
                    name="get_prompt",
                    version="1.2.3",
                )

            async def _list_tools(self):
                return [self._tool]

            async def _get_tool(self, name: str, version: VersionSpec | None = None):
                received_spec.append(version)
                if name == "get_prompt" and (
                    version is None or version.matches(self._tool.version)
                ):
                    return self._tool
                return None

        provider = VersionAwareProvider()
        provider.add_transform(RegexSearchTransform())

        server = FastMCP("Server", providers=[provider])

        @server.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        server.add_transform(PromptsAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            assert "get_prompt" in [t.name for t in tools]
            res = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(res.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]
            assert any(isinstance(spec, VersionSpec) for spec in received_spec)

    async def test_sequence_sensitive_catalog_transform_hiding_tool(self):
        """Sequence-sensitive transforms like catalog slicing are evaluated against full provider catalog."""
        from collections.abc import Sequence

        from fastmcp.server.transforms.catalog import CatalogTransform
        from fastmcp.tools.base import Tool

        class SlicingCatalogTransform(CatalogTransform):
            async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
                return tools[:1]

        server = FastMCP("Server")

        @server.tool
        def first_tool() -> str:
            return "first"

        @server.tool
        def get_prompt(query: str) -> str:
            return f"colliding: {query}"

        server.add_transform(SlicingCatalogTransform())

        @server.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        server.add_transform(PromptsAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert "first_tool" in names
            assert "get_prompt" in names

            res = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(res.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_namespaced_provider_user_tool_collision(self):
        """Collisions with user tools under a Namespace transform are recognized and prioritized."""
        sub = FastMCP("Sub")

        @sub.tool(name="prompt")
        def prompt_tool(name: str) -> str:
            return f"sub: {name}"

        server = FastMCP("Server")
        server.mount(sub, namespace="get")

        @server.prompt
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        server.add_transform(PromptsAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            get_tools = [t for t in tools if t.name == "get_prompt"]
            assert len(get_tools) == 1

            res = await client.call_tool("get_prompt", {"name": "custom_arg"})
            assert res.data == "sub: custom_arg"

    async def test_preceding_namespace_with_retaining_catalog_calls_user_tool(self):
        """When a preceding Namespace and a retaining CatalogTransform precede PromptsAsTools,
        the user tool is called using its query schema rather than falling back to synthetic tool."""
        from collections.abc import Sequence

        from fastmcp.server.transforms.catalog import CatalogTransform
        from fastmcp.server.transforms.namespace import Namespace
        from fastmcp.tools.base import Tool

        class RetainingCatalog(CatalogTransform):
            async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
                return tools

        server = FastMCP("Server")

        @server.tool(name="prompt")
        def user_prompt(query: str) -> str:
            return f"user: {query}"

        @server.prompt(name="greet")
        def greet_prompt(name: str = "World") -> str:
            return f"Hello, {name}!"

        server.add_transform(Namespace("get"))
        server.add_transform(RetainingCatalog())
        server.add_transform(PromptsAsTools(server))

        async with Client(server) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            res = await client.call_tool("get_prompt", {"query": "test"})
            assert res.data == "user: test"

    async def test_mounted_child_with_middleware_filtering_tool_uses_synthetic_tool(
        self,
    ):
        """When a mounted child has middleware filtering out a colliding tool, the parent's synthetic tool is used."""
        from collections.abc import Sequence
        from typing import Any

        from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
        from fastmcp.tools.base import Tool

        class FilterMiddleware(Middleware):
            async def on_list_tools(
                self,
                context: MiddlewareContext[Any],
                call_next: CallNext[Any, Sequence[Tool]],
            ) -> Sequence[Tool]:
                tools = await call_next(context)
                return [t for t in tools if t.name != "get_prompt"]

        child = FastMCP("Child")

        @child.tool(name="get_prompt")
        def child_prompt(query: str) -> str:
            return f"child: {query}"

        child.add_middleware(FilterMiddleware())

        parent = FastMCP("Parent")
        parent.mount(child)

        @parent.prompt(name="greet")
        def greet_prompt(name: str = "World") -> str:
            return f"Hello, {name}!"

        parent.add_transform(PromptsAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools]
            assert "get_prompt" in tool_names

            res = await client.call_tool(
                "get_prompt", {"name": "greet", "arguments": {"name": "Alice"}}
            )
            data = json.loads(res.data)
            assert "Hello, Alice!" in data["messages"][0]["content"]

    async def test_parent_search_transform_with_untransformed_mounted_child_pinned_tool(
        self,
    ):
        """When parent has a search transform with always_visible before PromptsAsTools,
        an untransformed mounted child tool in always_visible is preserved and called with user schema."""
        from fastmcp.server.transforms.search.regex import RegexSearchTransform

        child = FastMCP("Child")

        @child.tool(name="get_prompt")
        def child_prompt(query: str) -> str:
            return f"child: {query}"

        parent = FastMCP("Parent")
        parent.mount(child)

        @parent.prompt(name="greet")
        def greet_prompt(name: str = "World") -> str:
            return f"Hello, {name}!"

        parent.add_transform(RegexSearchTransform(always_visible=["get_prompt"]))
        parent.add_transform(PromptsAsTools(parent))

        async with Client(parent) as client:
            tools = await client.list_tools()
            matched = [t for t in tools if t.name == "get_prompt"]
            assert len(matched) == 1
            assert "query" in matched[0].input_schema["properties"]

            res = await client.call_tool("get_prompt", {"query": "hello"})
            assert res.data == "child: hello"


class TestListPromptsTool:
    """Test the list_prompts tool."""

    async def test_lists_prompts(self):
        """list_prompts returns prompt metadata."""
        mcp = FastMCP("Test")

        @mcp.prompt
        def analyze_code() -> str:
            """Analyze code for issues."""
            return "Analyze this code"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_prompts", {})
            prompts = json.loads(result.data)

            assert len(prompts) == 1
            assert prompts[0]["name"] == "analyze_code"
            assert prompts[0]["description"] == "Analyze code for issues."

    async def test_lists_prompt_with_arguments(self):
        """list_prompts includes argument metadata."""
        mcp = FastMCP("Test")

        @mcp.prompt
        def analyze_code(code: str, language: str = "python") -> str:
            """Analyze code for issues."""
            return f"Analyze this {language} code:\n{code}"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_prompts", {})
            prompts = json.loads(result.data)

            assert len(prompts) == 1
            args = prompts[0]["arguments"]
            assert len(args) == 2

            # Check required arg
            code_arg = next(a for a in args if a["name"] == "code")
            assert code_arg["required"] is True

            # Check optional arg
            lang_arg = next(a for a in args if a["name"] == "language")
            assert lang_arg["required"] is False

    async def test_lists_highest_version_only(self):
        """list_prompts shows one entry per name, like prompts/list does."""
        mcp = FastMCP("Test")

        @mcp.prompt(name="greet", version="1.0")
        def greet_v1(name: str) -> str:
            """Greet v1."""
            return f"Hi {name}"

        @mcp.prompt(name="greet", version="2.0")
        def greet_v2(name: str, formal: str = "no") -> str:
            """Greet v2."""
            return f"Hello {name}"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_prompts", {})
            prompts = json.loads(result.data)

            assert len(prompts) == 1
            assert prompts[0]["name"] == "greet"
            assert prompts[0]["description"] == "Greet v2."
            assert [a["name"] for a in prompts[0]["arguments"]] == ["name", "formal"]

    async def test_empty_when_no_prompts(self):
        """list_prompts returns empty list when no prompts exist."""
        mcp = FastMCP("Test")
        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("list_prompts", {})
            assert json.loads(result.data) == []


class TestGetPromptTool:
    """Test the get_prompt tool."""

    async def test_gets_prompt_without_arguments(self):
        """get_prompt gets a prompt with no arguments."""
        mcp = FastMCP("Test")

        @mcp.prompt
        def simple_prompt() -> str:
            """A simple prompt."""
            return "Hello, world!"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool("get_prompt", {"name": "simple_prompt"})
            response = json.loads(result.data)

            assert "messages" in response
            assert len(response["messages"]) == 1
            assert response["messages"][0]["role"] == "user"
            assert "Hello, world!" in response["messages"][0]["content"]

    async def test_gets_prompt_with_arguments(self):
        """get_prompt gets a prompt with arguments."""
        mcp = FastMCP("Test")

        @mcp.prompt
        def analyze_code(code: str, language: str = "python") -> str:
            """Analyze code."""
            return f"Analyze this {language} code:\n{code}"

        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_prompt",
                {
                    "name": "analyze_code",
                    "arguments": {"code": "x = 1", "language": "python"},
                },
            )
            response = json.loads(result.data)

            assert "messages" in response
            content = response["messages"][0]["content"]
            assert "python" in content
            assert "x = 1" in content

    async def test_error_on_unknown_prompt(self):
        """get_prompt raises error for unknown prompt name."""
        from fastmcp.exceptions import ToolError

        mcp = FastMCP("Test")
        mcp.add_transform(PromptsAsTools(mcp))

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="Unknown prompt"):
                await client.call_tool("get_prompt", {"name": "unknown_prompt"})


class TestPromptsAsToolsWithNamespace:
    """Test PromptsAsTools combined with other transforms."""

    async def test_works_with_namespace_on_provider(self):
        """PromptsAsTools works when provider has Namespace transform."""
        from fastmcp.server.providers import FastMCPProvider
        from fastmcp.server.transforms import Namespace

        sub = FastMCP("Sub")

        @sub.prompt
        def my_prompt() -> str:
            """A prompt."""
            return "Hello"

        main = FastMCP("Main")
        provider = FastMCPProvider(sub)
        provider.add_transform(Namespace("sub"))
        main.add_provider(provider)
        main.add_transform(PromptsAsTools(main))

        async with Client(main) as client:
            result = await client.call_tool("list_prompts", {})
            prompts = json.loads(result.data)

            # Prompt should have namespaced name
            assert len(prompts) == 1
            assert prompts[0]["name"] == "sub_my_prompt"


class TestPromptsAsToolsRepr:
    """Test PromptsAsTools repr."""

    def test_repr(self):
        """Transform has useful repr."""
        mcp = FastMCP("Test")
        transform = PromptsAsTools(mcp)
        assert "PromptsAsTools" in repr(transform)
