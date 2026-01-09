"""Tests for fastmcp.fs decorators."""

import pytest

from fastmcp.fs.decorators import (
    PromptMeta,
    ResourceMeta,
    ToolMeta,
    get_fs_meta,
    has_fs_meta,
    prompt,
    resource,
    tool,
)


class TestToolDecorator:
    """Tests for the @tool decorator."""

    def test_tool_without_parens(self):
        """@tool without parentheses should work."""

        @tool
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        assert has_fs_meta(greet)
        meta = get_fs_meta(greet)
        assert isinstance(meta, ToolMeta)
        assert meta.type == "tool"
        assert meta.name is None  # Will use function name

    def test_tool_with_empty_parens(self):
        """@tool() with empty parentheses should work."""

        @tool()
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        assert has_fs_meta(greet)
        meta = get_fs_meta(greet)
        assert isinstance(meta, ToolMeta)

    def test_tool_with_name_arg(self):
        """@tool("name") with name as first arg should work."""

        @tool("custom-greet")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        meta = get_fs_meta(greet)
        assert meta is not None
        assert meta.name == "custom-greet"

    def test_tool_with_name_kwarg(self):
        """@tool(name="name") with keyword arg should work."""

        @tool(name="custom-greet")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        meta = get_fs_meta(greet)
        assert meta is not None
        assert meta.name == "custom-greet"

    def test_tool_with_all_metadata(self):
        """@tool with all metadata should store it all."""

        @tool(
            name="custom-greet",
            title="Greeting Tool",
            description="Greets people",
            tags={"greeting", "demo"},
            meta={"custom": "value"},
        )
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        meta = get_fs_meta(greet)
        assert meta is not None
        assert meta.name == "custom-greet"
        assert meta.title == "Greeting Tool"
        assert meta.description == "Greets people"
        assert meta.tags == {"greeting", "demo"}
        assert meta.meta == {"custom": "value"}

    def test_tool_preserves_function(self):
        """@tool should preserve the original function."""

        @tool
        def greet(name: str) -> str:
            """Greet someone."""
            return f"Hello, {name}!"

        # Function should still work
        assert greet("World") == "Hello, World!"
        assert greet.__name__ == "greet"
        assert greet.__doc__ == "Greet someone."


class TestResourceDecorator:
    """Tests for the @resource decorator."""

    def test_resource_requires_uri(self):
        """@resource should require a URI argument."""
        with pytest.raises(TypeError, match="requires a URI"):

            @resource  # type: ignore[arg-type]
            def get_config() -> str:
                return "{}"

    def test_resource_with_uri(self):
        """@resource("uri") should store the URI."""

        @resource("config://app")
        def get_config() -> dict:
            return {"setting": "value"}

        assert has_fs_meta(get_config)
        meta = get_fs_meta(get_config)
        assert isinstance(meta, ResourceMeta)
        assert meta.type == "resource"
        assert meta.uri == "config://app"

    def test_resource_with_template_uri(self):
        """@resource with template URI should work."""

        @resource("users://{user_id}/profile")
        def get_profile(user_id: str) -> dict:
            return {"id": user_id}

        meta = get_fs_meta(get_profile)
        assert isinstance(meta, ResourceMeta)
        assert meta.uri == "users://{user_id}/profile"

    def test_resource_with_all_metadata(self):
        """@resource with all metadata should store it all."""

        @resource(
            "config://app",
            name="app-config",
            title="Application Config",
            description="Gets app configuration",
            mime_type="application/json",
            tags={"config"},
            meta={"custom": "value"},
        )
        def get_config() -> dict:
            return {"setting": "value"}

        meta = get_fs_meta(get_config)
        assert isinstance(meta, ResourceMeta)
        assert meta.uri == "config://app"
        assert meta.name == "app-config"
        assert meta.title == "Application Config"
        assert meta.description == "Gets app configuration"
        assert meta.mime_type == "application/json"
        assert meta.tags == {"config"}
        assert meta.meta == {"custom": "value"}

    def test_resource_preserves_function(self):
        """@resource should preserve the original function."""

        @resource("config://app")
        def get_config() -> dict:
            """Get config."""
            return {"setting": "value"}

        # Function should still work
        assert get_config() == {"setting": "value"}
        assert get_config.__name__ == "get_config"
        assert get_config.__doc__ == "Get config."


class TestPromptDecorator:
    """Tests for the @prompt decorator."""

    def test_prompt_without_parens(self):
        """@prompt without parentheses should work."""

        @prompt
        def analyze(topic: str) -> list:
            return [{"role": "user", "content": f"Analyze: {topic}"}]

        assert has_fs_meta(analyze)
        meta = get_fs_meta(analyze)
        assert isinstance(meta, PromptMeta)
        assert meta.type == "prompt"
        assert meta.name is None

    def test_prompt_with_empty_parens(self):
        """@prompt() with empty parentheses should work."""

        @prompt()
        def analyze(topic: str) -> list:
            return [{"role": "user", "content": f"Analyze: {topic}"}]

        assert has_fs_meta(analyze)
        meta = get_fs_meta(analyze)
        assert isinstance(meta, PromptMeta)

    def test_prompt_with_name_arg(self):
        """@prompt("name") with name as first arg should work."""

        @prompt("custom-analyze")
        def analyze(topic: str) -> list:
            return [{"role": "user", "content": f"Analyze: {topic}"}]

        meta = get_fs_meta(analyze)
        assert meta is not None
        assert meta.name == "custom-analyze"

    def test_prompt_with_name_kwarg(self):
        """@prompt(name="name") with keyword arg should work."""

        @prompt(name="custom-analyze")
        def analyze(topic: str) -> list:
            return [{"role": "user", "content": f"Analyze: {topic}"}]

        meta = get_fs_meta(analyze)
        assert meta is not None
        assert meta.name == "custom-analyze"

    def test_prompt_with_all_metadata(self):
        """@prompt with all metadata should store it all."""

        @prompt(
            name="custom-analyze",
            title="Analysis Prompt",
            description="Analyzes topics",
            tags={"analysis"},
            meta={"custom": "value"},
        )
        def analyze(topic: str) -> list:
            return [{"role": "user", "content": f"Analyze: {topic}"}]

        meta = get_fs_meta(analyze)
        assert meta is not None
        assert meta.name == "custom-analyze"
        assert meta.title == "Analysis Prompt"
        assert meta.description == "Analyzes topics"
        assert meta.tags == {"analysis"}
        assert meta.meta == {"custom": "value"}

    def test_prompt_preserves_function(self):
        """@prompt should preserve the original function."""

        @prompt
        def analyze(topic: str) -> list:
            """Analyze a topic."""
            return [{"role": "user", "content": f"Analyze: {topic}"}]

        # Function should still work
        result = analyze("Python")
        assert result == [{"role": "user", "content": "Analyze: Python"}]
        assert analyze.__name__ == "analyze"
        assert analyze.__doc__ == "Analyze a topic."


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_has_fs_meta_false_for_undecorated(self):
        """has_fs_meta should return False for undecorated functions."""

        def plain_function():
            pass

        assert not has_fs_meta(plain_function)

    def test_get_fs_meta_none_for_undecorated(self):
        """get_fs_meta should return None for undecorated functions."""

        def plain_function():
            pass

        assert get_fs_meta(plain_function) is None
