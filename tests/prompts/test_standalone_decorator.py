"""Tests for the standalone @prompt decorator.

The @prompt decorator creates FunctionPrompt objects without registering them
to a server. Objects can be added explicitly via server.add_prompt() or
discovered by FileSystemProvider.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.prompts import FunctionPrompt, prompt


class TestPromptDecorator:
    """Tests for the @prompt decorator."""

    def test_prompt_without_parens(self):
        """@prompt without parentheses should create a FunctionPrompt."""

        @prompt
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        assert isinstance(analyze, FunctionPrompt)
        assert analyze.name == "analyze"

    def test_prompt_with_empty_parens(self):
        """@prompt() with empty parentheses should create a FunctionPrompt."""

        @prompt()
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        assert isinstance(analyze, FunctionPrompt)
        assert analyze.name == "analyze"

    def test_prompt_with_name_arg(self):
        """@prompt("name") with name as first arg should work."""

        @prompt("custom-analyze")
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        assert isinstance(analyze, FunctionPrompt)
        assert analyze.name == "custom-analyze"

    def test_prompt_with_name_kwarg(self):
        """@prompt(name="name") with keyword arg should work."""

        @prompt(name="custom-analyze")
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        assert isinstance(analyze, FunctionPrompt)
        assert analyze.name == "custom-analyze"

    def test_prompt_with_all_metadata(self):
        """@prompt with all metadata should store it all."""

        @prompt(
            name="custom-analyze",
            title="Analysis Prompt",
            description="Analyzes topics",
            tags={"analysis"},
            meta={"custom": "value"},
        )
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        assert isinstance(analyze, FunctionPrompt)
        assert analyze.name == "custom-analyze"
        assert analyze.title == "Analysis Prompt"
        assert analyze.description == "Analyzes topics"
        assert analyze.tags == {"analysis"}
        assert analyze.meta == {"custom": "value"}

    async def test_prompt_can_be_rendered(self):
        """Prompt created by @prompt should be renderable."""

        @prompt
        def analyze(topic: str) -> str:
            """Analyze a topic."""
            return f"Analyze: {topic}"

        result = await analyze.render({"topic": "Python"})
        assert result.messages[0].content.text == "Analyze: Python"  # type: ignore[union-attr]

    def test_prompt_rejects_classmethod_decorator(self):
        """@prompt should reject classmethod-decorated functions."""
        with pytest.raises(TypeError, match="classmethod"):

            class MyClass:
                @prompt  # type: ignore[arg-type]
                @classmethod
                def my_prompt(cls) -> str:
                    return "hello"

    def test_prompt_with_both_name_args_raises(self):
        """@prompt should raise if both positional and keyword name are given."""
        with pytest.raises(TypeError, match="Cannot specify both"):

            @prompt("name1", name="name2")  # type: ignore[call-overload]
            def my_prompt() -> str:
                return "hello"

    async def test_prompt_added_to_server(self):
        """Prompt created by @prompt should work when added to a server."""

        @prompt
        def analyze(topic: str) -> str:
            """Analyze a topic."""
            return f"Please analyze: {topic}"

        mcp = FastMCP("Test")
        mcp.add_prompt(analyze)

        async with Client(mcp) as client:
            prompts = await client.list_prompts()
            assert any(p.name == "analyze" for p in prompts)

            result = await client.get_prompt("analyze", {"topic": "Python"})
            assert "Python" in str(result)
