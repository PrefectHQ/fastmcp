import pytest
from mcp.types import EmbeddedResource, TextResourceContents
from pydantic import FileUrl

from fastmcp.prompts.prompt import (
    Message,
    Prompt,
    PromptMessage,
    TextContent,
)


class TestRenderPrompt:
    async def test_basic_fn(self):
        def fn() -> str:
            return "Hello, world!"

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, world!")
            )
        ]

    async def test_async_fn(self):
        async def fn() -> str:
            return "Hello, world!"

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, world!")
            )
        ]

    async def test_fn_with_args(self):
        async def fn(name: str, age: int = 30) -> str:
            return f"Hello, {name}! You're {age} years old."

        prompt = Prompt.from_function(fn)
        assert await prompt.render(arguments=dict(name="World")) == [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text", text="Hello, World! You're 30 years old."
                ),
            )
        ]

    async def test_callable_object(self):
        class MyPrompt:
            def __call__(self, name: str) -> str:
                return f"Hello, {name}!"

        prompt = Prompt.from_function(MyPrompt())
        assert await prompt.render(arguments=dict(name="World")) == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, World!")
            )
        ]

    async def test_async_callable_object(self):
        class MyPrompt:
            async def __call__(self, name: str) -> str:
                return f"Hello, {name}!"

        prompt = Prompt.from_function(MyPrompt())
        assert await prompt.render(arguments=dict(name="World")) == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, World!")
            )
        ]

    async def test_fn_with_invalid_kwargs(self):
        async def fn(name: str, age: int = 30) -> str:
            return f"Hello, {name}! You're {age} years old."

        prompt = Prompt.from_function(fn)
        with pytest.raises(ValueError):
            await prompt.render(arguments=dict(age=40))

    async def test_fn_returns_message(self):
        async def fn() -> PromptMessage:
            return PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, world!")
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user", content=TextContent(type="text", text="Hello, world!")
            )
        ]

    async def test_fn_returns_assistant_message(self):
        async def fn() -> PromptMessage:
            return PromptMessage(
                role="assistant", content=TextContent(type="text", text="Hello, world!")
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="assistant", content=TextContent(type="text", text="Hello, world!")
            )
        ]

    async def test_fn_returns_multiple_messages(self):
        expected = [
            Message(role="user", content="Hello, world!"),
            Message(role="assistant", content="How can I help you today?"),
            Message(
                role="user",
                content="I'm looking for a restaurant in the center of town.",
            ),
        ]

        async def fn() -> list[PromptMessage]:
            return expected

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == expected

    async def test_fn_returns_list_of_strings(self):
        expected = [
            "Hello, world!",
            "I'm looking for a restaurant in the center of town.",
        ]

        async def fn() -> list[str]:
            return expected

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(role="user", content=TextContent(type="text", text=t))
            for t in expected
        ]

    async def test_fn_returns_resource_content(self):
        """Test returning a message with resource content."""

        async def fn() -> PromptMessage:
            return PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )
        ]

    async def test_fn_returns_mixed_content(self):
        """Test returning messages with mixed content types."""

        async def fn() -> list[PromptMessage | str]:
            return [
                "Please analyze this file:",
                PromptMessage(
                    role="user",
                    content=EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            uri=FileUrl("file://file.txt"),
                            text="File contents",
                            mimeType="text/plain",
                        ),
                    ),
                ),
                Message(role="assistant", content="I'll help analyze that file."),
            ]

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user",
                content=TextContent(type="text", text="Please analyze this file:"),
            ),
            PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            ),
            PromptMessage(
                role="assistant",
                content=TextContent(type="text", text="I'll help analyze that file."),
            ),
        ]

    async def test_fn_returns_message_with_resource(self):
        """Test returning a dict with resource content."""

        async def fn() -> PromptMessage:
            return PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            PromptMessage(
                role="user",
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                ),
            )
        ]
