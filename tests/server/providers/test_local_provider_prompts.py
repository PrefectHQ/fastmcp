"""Tests for prompt behavior in LocalProvider.

Tests cover:
- Prompt context injection
- Prompt decorator patterns
"""

import pytest
from mcp.types import TextContent

from fastmcp import Client, Context, FastMCP
from fastmcp.prompts.prompt import FunctionPrompt, Prompt, PromptResult


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

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.name == "fn"
        content = await prompt.render()
        if not isinstance(content, PromptResult):
            content = PromptResult.from_value(content)
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_without_parentheses(self):
        mcp = FastMCP()

        @mcp.prompt
        def fn() -> str:
            return "Hello, world!"

        prompts = await mcp.get_prompts()
        assert "fn" in prompts

        async with Client(mcp) as client:
            result = await client.get_prompt("fn")
            assert len(result.messages) == 1
            assert isinstance(result.messages[0].content, TextContent)
            assert result.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.prompt(name="custom_name")
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["custom_name"]
        assert prompt.name == "custom_name"
        content = await prompt.render()
        if not isinstance(content, PromptResult):
            content = PromptResult.from_value(content)
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.prompt(description="A custom description")
        def fn() -> str:
            return "Hello, world!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["fn"]
        assert prompt.description == "A custom description"
        content = await prompt.render()
        if not isinstance(content, PromptResult):
            content = PromptResult.from_value(content)
        assert isinstance(content.messages[0].content, TextContent)
        assert content.messages[0].content.text == "Hello, world!"

    async def test_prompt_decorator_with_parameters(self):
        mcp = FastMCP()

        @mcp.prompt
        def test_prompt(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["test_prompt"]
        assert prompt.arguments is not None
        assert len(prompt.arguments) == 2
        assert prompt.arguments[0].name == "name"
        assert prompt.arguments[0].required is True
        assert prompt.arguments[1].name == "greeting"
        assert prompt.arguments[1].required is False

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt", {"name": "World"})
            assert len(result.messages) == 1
            message = result.messages[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Hello, World!"

            result = await client.get_prompt(
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

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
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

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Class prefix: Hello, world!"

    async def test_prompt_decorator_classmethod_error(self):
        mcp = FastMCP()

        with pytest.raises(ValueError, match="To decorate a classmethod"):

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

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert isinstance(message.content, TextContent)
            assert message.content.text == "Static Hello, world!"

    async def test_prompt_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.prompt
        async def test_prompt() -> str:
            return "Async Hello, world!"

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
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

        prompts_dict = await mcp.get_prompts()
        assert len(prompts_dict) == 1
        prompt = prompts_dict["sample_prompt"]
        assert prompt.tags == {"example", "test-tag"}

    async def test_prompt_decorator_with_string_name(self):
        """Test that @prompt(\"custom_name\") syntax works correctly."""
        mcp = FastMCP()

        @mcp.prompt("string_named_prompt")
        def my_function() -> str:
            """A function with a string name."""
            return "Hello from string named prompt!"

        prompts = await mcp.get_prompts()
        assert "string_named_prompt" in prompts
        assert "my_function" not in prompts

        async with Client(mcp) as client:
            result = await client.get_prompt("string_named_prompt")
            assert len(result.messages) == 1
            assert isinstance(result.messages[0].content, TextContent)
            assert result.messages[0].content.text == "Hello from string named prompt!"

    async def test_prompt_direct_function_call(self):
        """Test that prompts can be registered via direct function call."""
        mcp = FastMCP()

        def standalone_function() -> str:
            """A standalone function to be registered."""
            return "Hello from direct call!"

        result_fn = mcp.prompt(standalone_function, name="direct_call_prompt")

        assert isinstance(result_fn, FunctionPrompt)

        prompts = await mcp.get_prompts()
        assert prompts["direct_call_prompt"] is result_fn

        async with Client(mcp) as client:
            result = await client.get_prompt("direct_call_prompt")
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
            @mcp.prompt  # type: ignore[misc]
            @staticmethod
            def test_prompt() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.get_prompt("test_prompt")
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

        prompts_dict = await mcp.get_prompts()
        prompt = prompts_dict["test_prompt"]

        assert prompt.meta == meta_data
