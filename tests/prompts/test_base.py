import pytest
from mcp.types import EmbeddedResource, TextResourceContents
from pydantic import FileUrl

from fastmcp.prompts.prompt import (
    AssistantMessage,
    Message,
    Prompt,
    TextContent,
    UserMessage,
)


class TestRenderPrompt:
    async def test_basic_fn(self):
        def fn() -> str:
            return "Hello, world!"

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            UserMessage(content=TextContent(type="text", text="Hello, world!"))
        ]

    async def test_async_fn(self):
        async def fn() -> str:
            return "Hello, world!"

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            UserMessage(content=TextContent(type="text", text="Hello, world!"))
        ]

    async def test_fn_with_args(self):
        async def fn(name: str, age: int = 30) -> str:
            return f"Hello, {name}! You're {age} years old."

        prompt = Prompt.from_function(fn)
        assert await prompt.render(arguments=dict(name="World")) == [
            UserMessage(
                content=TextContent(
                    type="text", text="Hello, World! You're 30 years old."
                )
            )
        ]

    async def test_fn_with_invalid_kwargs(self):
        async def fn(name: str, age: int = 30) -> str:
            return f"Hello, {name}! You're {age} years old."

        prompt = Prompt.from_function(fn)
        with pytest.raises(ValueError):
            await prompt.render(arguments=dict(age=40))

    async def test_fn_returns_message(self):
        async def fn() -> Message:
            return UserMessage(content="Hello, world!")

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            UserMessage(content=TextContent(type="text", text="Hello, world!"))
        ]

    async def test_fn_returns_assistant_message(self):
        async def fn() -> Message:
            return AssistantMessage(
                content=TextContent(type="text", text="Hello, world!")
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            AssistantMessage(content=TextContent(type="text", text="Hello, world!"))
        ]

    async def test_fn_returns_multiple_messages(self):
        expected = [
            UserMessage("Hello, world!"),
            AssistantMessage("How can I help you today?"),
            UserMessage("I'm looking for a restaurant in the center of town."),
        ]

        async def fn() -> list[Message]:
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
        assert await prompt.render() == [UserMessage(t) for t in expected]

    async def test_fn_returns_resource_content(self):
        """Test returning a message with resource content."""

        async def fn() -> Message:
            return UserMessage(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                )
            )

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            UserMessage(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                )
            )
        ]

    async def test_fn_returns_mixed_content(self):
        """Test returning messages with mixed content types."""

        async def fn() -> list[Message]:
            return [
                UserMessage(content="Please analyze this file:"),
                UserMessage(
                    content=EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            uri=FileUrl("file://file.txt"),
                            text="File contents",
                            mimeType="text/plain",
                        ),
                    )
                ),
                AssistantMessage(content="I'll help analyze that file."),
            ]

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            UserMessage(
                content=TextContent(type="text", text="Please analyze this file:")
            ),
            UserMessage(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                )
            ),
            AssistantMessage(
                content=TextContent(type="text", text="I'll help analyze that file.")
            ),
        ]

    async def test_fn_returns_dict_with_resource(self):
        """Test returning a dict with resource content."""

        async def fn() -> dict:
            return {
                "role": "user",
                "content": {
                    "type": "resource",
                    "resource": {
                        "uri": FileUrl("file://file.txt"),
                        "text": "File contents",
                        "mimeType": "text/plain",
                    },
                },
            }

        prompt = Prompt.from_function(fn)
        assert await prompt.render() == [
            UserMessage(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=FileUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                )
            )
        ]
