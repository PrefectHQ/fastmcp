"""Tests for PromptsAsTools transform."""

import json

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.server.transforms import PromptsAsTools


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
