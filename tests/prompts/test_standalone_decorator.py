"""Tests for the standalone @prompt decorator.

The @prompt decorator attaches metadata to functions without registering them
to a server. Functions can be added explicitly via server.add_prompt() or
discovered by FileSystemProvider.
"""

from typing import cast

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.prompts import prompt
from fastmcp.prompts.function_prompt import DecoratedPrompt, PromptMeta


class TestPromptDecorator:
    """Tests for the @prompt decorator."""

    def test_prompt_without_parens(self):
        """@prompt without parentheses should attach metadata."""

        @prompt
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        decorated = cast(DecoratedPrompt, analyze)
        assert callable(analyze)
        assert hasattr(analyze, "__fastmcp__")
        assert isinstance(decorated.__fastmcp__, PromptMeta)
        assert decorated.__fastmcp__.name is None  # Uses function name by default

    def test_prompt_with_empty_parens(self):
        """@prompt() with empty parentheses should attach metadata."""

        @prompt()
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        decorated = cast(DecoratedPrompt, analyze)
        assert callable(analyze)
        assert hasattr(analyze, "__fastmcp__")
        assert isinstance(decorated.__fastmcp__, PromptMeta)

    def test_prompt_with_name_arg(self):
        """@prompt("name") with name as first arg should work."""

        @prompt("custom-analyze")
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        decorated = cast(DecoratedPrompt, analyze)
        assert callable(analyze)
        assert hasattr(analyze, "__fastmcp__")
        assert decorated.__fastmcp__.name == "custom-analyze"

    def test_prompt_with_name_kwarg(self):
        """@prompt(name="name") with keyword arg should work."""

        @prompt(name="custom-analyze")
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        decorated = cast(DecoratedPrompt, analyze)
        assert callable(analyze)
        assert hasattr(analyze, "__fastmcp__")
        assert decorated.__fastmcp__.name == "custom-analyze"

    def test_prompt_with_all_metadata(self):
        """@prompt with all metadata should store it all."""

        @prompt(
            name="custom-analyze",
            title="Analysis Prompt",
            description="Analyzes topics",
            tags={"analysis", "demo"},
            meta={"custom": "value"},
        )
        def analyze(topic: str) -> str:
            return f"Analyze: {topic}"

        decorated = cast(DecoratedPrompt, analyze)
        assert callable(analyze)
        assert hasattr(analyze, "__fastmcp__")
        assert decorated.__fastmcp__.name == "custom-analyze"
        assert decorated.__fastmcp__.title == "Analysis Prompt"
        assert decorated.__fastmcp__.description == "Analyzes topics"
        assert decorated.__fastmcp__.tags == {"analysis", "demo"}
        assert decorated.__fastmcp__.meta == {"custom": "value"}

    async def test_prompt_function_still_callable(self):
        """Decorated function should still be directly callable."""

        @prompt
        def analyze(topic: str) -> str:
            """Analyze a topic."""
            return f"Please analyze: {topic}"

        # The function is still callable even though it has metadata
        result = cast(DecoratedPrompt, analyze)("Python")
        assert result == "Please analyze: Python"

    def test_prompt_rejects_classmethod_decorator(self):
        """@prompt should reject classmethod-decorated functions."""
        with pytest.raises(TypeError, match="classmethod"):

            class MyClass:
                @prompt
                @classmethod
                def my_prompt(cls, topic: str) -> str:
                    return f"Analyze: {topic}"

    def test_prompt_with_both_name_args_raises(self):
        """@prompt should raise if both positional and keyword name are given."""
        with pytest.raises(TypeError, match="Cannot specify.*both.*argument.*keyword"):

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

            result = await client.get_prompt("analyze", {"topic": "FastMCP"})
            assert "FastMCP" in str(result)
