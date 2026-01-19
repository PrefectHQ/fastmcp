"""Tests for prompt behavior in LocalProvider.

Tests cover:
- Prompt context injection
- Prompt decorator patterns
"""

import pytest
from mcp.types import TextContent

from fastmcp import Client, Context, FastMCP
from fastmcp.prompts.prompt import Prompt


class TestPromptContext:
    async def test_prompt_context(self):
        mcp = FastMCP()

        @mcp.prompt
        def prompt_fn(name: str, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return f"Hello, {name}! {ctx.request_id}"

        async with Client(mcp) as client:
            result = await client.get_prompt("prompt_fn", {"name": "World"})
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.role == "user"

    async def test_prompt_context_with_callable_object(self):
        mcp = FastMCP()

        class MyPrompt:
            def __call__(self, name: str, ctx: Context) -> str:
                return f"Hello, {name}! {ctx.request_id}"

        mcp.add_prompt(Prompt.from_function(MyPrompt(), name="my_prompt"))

        async with Client(mcp) as client:
            result = await client.get_prompt("my_prompt", {"name": "World"})
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.role == "user"
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Hello, World! 1"


class TestPromptDecorator:
    async def test_prompt_decorator(self):
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> str:
            return "Hello, world!"

        prompts = await mcp.list_prompts()
        assert len(prompts) == 1
        prompt = next(p for p in prompts if p.name == "fn")
        assert prompt.name == "fn"
        content = await prompt.render()
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_without_parentheses(self):
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> str:
            return "Hello, world!"

        prompts = await mcp.list_prompts()
        assert any(p.name == "fn" for p in prompts)

        result = await mcp.render_prompt("fn")
        assert len(result.messages) == 1
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.prompt(name="custom_name")
        def fn() -> str:
            return "Hello, world!"

        prompts_list = await mcp.list_prompts()
        assert len(prompts_list) == 1
        prompt = next(p for p in prompts_list if p.name == "custom_name")
        assert prompt.name == "custom_name"
        content = await prompt.render()
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.prompt(description="A custom description")
        def fn() -> str:
            return "Hello, world!"

        prompts_list = await mcp.list_prompts()
        assert len(prompts_list) == 1
        prompt = next(p for p in prompts_list if p.name == "fn")
        assert prompt.description == "A custom description"
        content = await prompt.render()
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_parameters(self):
        mcp = FastMCP()

        @mcp.prompt
        def test_prompt(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        prompts = await mcp.list_prompts()
        assert len(prompts) == 1
        prompt = next(p for p in prompts if p.name == "test_prompt")
        assert prompt.arguments is not None
        assert len(prompt.arguments) == 2
        assert prompt.arguments[0].name == "name"
        assert prompt.arguments[0].required is True
        assert prompt.arguments[1].name == "greeting"
        assert prompt.arguments[1].required is False

        result = await mcp.render_prompt("test_prompt", {"name": "World"})
        assert len(result.messages) == 1
        message = result.messages[0]
        assert isinstance(message.content, TextContent)
        assert message.content.text == "Hello, World!"

        result = await mcp.render_prompt(
            "test_prompt", {"name": "World", "greeting": "Hi"}
        )
        assert len(result.messages) == 1
        message = result.messages[0]
        assert isinstance(message.content, TextContent)
        assert message.content.text == "Hi, World!"

    async def test_prompt_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, prefix: str):
                self.prefix = prefix

            def test_prompt(self) -> str:
                return f"{self.prefix} Hello, world!"

        obj = MyClass("My prefix:")
        mcp.add_prompt(Prompt.from_function(obj.test_prompt, name="test_prompt"))

        result = await mcp.render_prompt("test_prompt")
        assert len(result.messages) == 1
        message = result.messages[0]
        assert isinstance(message.content, TextContent)
        assert message.content.text == "My prefix: Hello, world!"

    async def test_prompt_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def test_prompt(cls) -> str:
                return f"{cls.prefix} Hello, world!"

        mcp.add_prompt(Prompt.from_function(MyClass.test_prompt, name="test_prompt"))

        result = await mcp.render_prompt("test_prompt")
        assert len(result.messages) == 1
        message = result.messages[0]
        assert isinstance(message.content, TextContent)
        assert message.content.text == "Class prefix: Hello, world!"

    async def test_prompt_decorator_classmethod_error(self):
        mcp = FastMCP()

        with pytest.raises(TypeError, match="classmethod"):

            class MyClass:
                @mcp.prompt
                @classmethod
                def test_prompt(cls) -> None:
                    pass

    async def test_prompt_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.prompt
            @staticmethod
            def test_prompt() -> str:
                return "Static Hello, world!"

        result = await mcp.render_prompt("test_prompt")
        assert len(result.messages) == 1
        message = result.messages[0]
        assert isinstance(message.content, TextContent)
        assert message.content.text == "Static Hello, world!"

    async def test_prompt_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.prompt
        async def test_prompt() -> str:
            return "Async Hello, world!"

        result = await mcp.render_prompt("test_prompt")
        assert len(result.messages) == 1
        message = result.messages[0]
        assert isinstance(message.content, TextContent)
        assert message.content.text == "Async Hello, world!"

    async def test_prompt_decorator_with_tags(self):
        """Test that the prompt decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.prompt(tags={"example", "test-tag"})
        def sample_prompt() -> str:
            return "Hello, world!"

        prompts = await mcp.list_prompts()
        assert len(prompts) == 1
        prompt = next(p for p in prompts if p.name == "sample_prompt")
        assert prompt.tags == {"example", "test-tag"}

    async def test_prompt_decorator_with_string_name(self):
        """Test that @prompt(\"custom_name\") syntax works correctly."""
        mcp = FastMCP()

        @mcp.prompt("string_named_prompt")
        def my_function() -> str:
            """A function with a string name."""
            return "Hello from string named prompt!"

        prompts = await mcp.list_prompts()
        assert any(p.name == "string_named_prompt" for p in prompts)
        assert not any(p.name == "my_function" for p in prompts)

        result = await mcp.render_prompt("string_named_prompt")
        assert len(result.messages) == 1
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Hello from string named prompt!"

    async def test_prompt_direct_function_call(self):
        """Test that prompts can be registered via direct function call."""
        from typing import cast

        from fastmcp.prompts.function_prompt import DecoratedPrompt

        mcp = FastMCP()

        def standalone_function() -> str:
            """A standalone function to be registered."""
            return "Hello from direct call!"

        result_fn = mcp.prompt(standalone_function, name="direct_call_prompt")

        # In new decorator mode, returns the function with metadata
        decorated = cast(DecoratedPrompt, result_fn)
        assert hasattr(result_fn, "__fastmcp__")
        assert decorated.__fastmcp__.name == "direct_call_prompt"
        assert result_fn is standalone_function

        prompts = await mcp.list_prompts()
        prompt = next(p for p in prompts if p.name == "direct_call_prompt")
        # Prompt is registered separately, not same object as decorated function
        assert prompt.name == "direct_call_prompt"

        result = await mcp.render_prompt("direct_call_prompt")
        assert len(result.messages) == 1
        assert isinstance(result.messages[0].content, TextContent)
        assert result.messages[0].content.text == "Hello from direct call!"

    async def test_prompt_decorator_conflicting_names_error(self):
        """Test that providing both positional and keyword names raises an error."""
        mcp = FastMCP()

        with pytest.raises(
            TypeError,
            match="Cannot specify both a name as first argument and as keyword argument",
        ):

            @mcp.prompt("positional_name", name="keyword_name")
            def my_function() -> str:
                return "Hello, world!"

    async def test_prompt_decorator_staticmethod_order(self):
        """Test that both decorator orders work for static methods"""
        mcp = FastMCP()

        class MyClass:
            @mcp.prompt
            @staticmethod
            def test_prompt() -> str:
                return "Static Hello, world!"

        result = await mcp.render_prompt("test_prompt")
        assert len(result.messages) == 1
        message = result.messages[0]
        assert isinstance(message.content, TextContent)
        assert message.content.text == "Static Hello, world!"

    async def test_prompt_decorator_with_meta(self):
        """Test that meta parameter is passed through the prompt decorator."""
        mcp = FastMCP()

        meta_data = {"version": "3.0", "type": "prompt"}

        @mcp.prompt(meta=meta_data)
        def test_prompt(message: str) -> str:
            return f"Response: {message}"

        prompts = await mcp.list_prompts()
        prompt = next(p for p in prompts if p.name == "test_prompt")

        assert prompt.meta == meta_data


class TestPromptEnabled:
    async def test_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        prompts = await mcp.list_prompts()
        assert any(p.name == "sample_prompt" for p in prompts)

        mcp.disable(names={"sample_prompt"}, components={"prompt"})

        prompts = await mcp.list_prompts()
        assert not any(p.name == "sample_prompt" for p in prompts)

        mcp.enable(names={"sample_prompt"}, components={"prompt"})

        prompts = await mcp.list_prompts()
        assert any(p.name == "sample_prompt" for p in prompts)

    async def test_prompt_disabled(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        mcp.disable(names={"sample_prompt"}, components={"prompt"})
        prompts = await mcp.list_prompts()
        assert len(prompts) == 0

    async def test_prompt_toggle_enabled(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        mcp.disable(names={"sample_prompt"}, components={"prompt"})
        prompts = await mcp.list_prompts()
        assert not any(p.name == "sample_prompt" for p in prompts)

        mcp.enable(names={"sample_prompt"}, components={"prompt"})
        prompts = await mcp.list_prompts()
        assert len(prompts) == 1

    async def test_prompt_toggle_disabled(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        mcp.disable(names={"sample_prompt"}, components={"prompt"})
        prompts = await mcp.list_prompts()
        assert len(prompts) == 0

        # get_prompt() applies enabled transform, returns None for disabled
        prompt = await mcp.get_prompt("sample_prompt")
        assert prompt is None

    async def test_get_prompt_and_disable(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        prompt = await mcp.get_prompt("sample_prompt")
        assert prompt is not None

        mcp.disable(names={"sample_prompt"}, components={"prompt"})
        prompts = await mcp.list_prompts()
        assert len(prompts) == 0

        # get_prompt() applies enabled transform, returns None for disabled
        prompt = await mcp.get_prompt("sample_prompt")
        assert prompt is None

    async def test_cant_get_disabled_prompt(self):
        mcp = FastMCP()

        @mcp.prompt
        def sample_prompt() -> str:
            return "Hello, world!"

        mcp.disable(names={"sample_prompt"}, components={"prompt"})

        # get_prompt() applies enabled transform, returns None for disabled
        prompt = await mcp.get_prompt("sample_prompt")
        assert prompt is None


class TestPromptTags:
    def create_server(self, include_tags=None, exclude_tags=None):
        mcp = FastMCP(include_tags=include_tags, exclude_tags=exclude_tags)

        @mcp.prompt(tags={"a", "b"})
        def prompt_1() -> str:
            return "1"

        @mcp.prompt(tags={"b", "c"})
        def prompt_2() -> str:
            return "2"

        return mcp

    async def test_include_tags_all_prompts(self):
        mcp = self.create_server(include_tags={"a", "b"})
        prompts = await mcp.list_prompts()
        assert {p.name for p in prompts} == {"prompt_1", "prompt_2"}

    async def test_include_tags_some_prompts(self):
        mcp = self.create_server(include_tags={"a"})
        prompts = await mcp.list_prompts()
        assert {p.name for p in prompts} == {"prompt_1"}

    async def test_exclude_tags_all_prompts(self):
        mcp = self.create_server(exclude_tags={"a", "b"})
        prompts = await mcp.list_prompts()
        assert {p.name for p in prompts} == set()

    async def test_exclude_tags_some_prompts(self):
        mcp = self.create_server(exclude_tags={"a"})
        prompts = await mcp.list_prompts()
        assert {p.name for p in prompts} == {"prompt_2"}

    async def test_exclude_takes_precedence_over_include(self):
        mcp = self.create_server(exclude_tags={"a"}, include_tags={"b"})
        prompts = await mcp.list_prompts()
        assert {p.name for p in prompts} == {"prompt_2"}

    async def test_read_prompt_includes_tags(self):
        mcp = self.create_server(include_tags={"a"})
        # _get_prompt applies enabled transform (tag filtering)
        prompt = await mcp._get_prompt("prompt_1")
        result = await prompt.render({})
        assert result.messages[0].content.text == "1"

        prompt = await mcp.get_prompt("prompt_2")
        assert prompt is None

    async def test_read_prompt_excludes_tags(self):
        mcp = self.create_server(exclude_tags={"a"})
        # get_prompt applies enabled transform (tag filtering)
        prompt = await mcp.get_prompt("prompt_1")
        assert prompt is None

        prompt = await mcp.get_prompt("prompt_2")
        result = await prompt.render({})
        assert result.messages[0].content.text == "2"
