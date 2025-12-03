import json
from typing import cast
from unittest.mock import AsyncMock

import pytest
from mcp.types import TextContent
from pydantic_core import to_json

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams
from fastmcp.utilities.types import Image


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP()

    @mcp.tool
    async def simple_sample(message: str, context: Context) -> str:
        result = await context.sample("Hello, world!")
        return result.text  # type: ignore[attr-defined]

    @mcp.tool
    async def sample_with_system_prompt(message: str, context: Context) -> str:
        result = await context.sample("Hello, world!", system_prompt="You love FastMCP")
        return result.text  # type: ignore[attr-defined]

    @mcp.tool
    async def sample_with_messages(message: str, context: Context) -> str:
        result = await context.sample(
            [
                "Hello!",
                SamplingMessage(
                    content=TextContent(
                        type="text", text="How can I assist you today?"
                    ),
                    role="assistant",
                ),
            ]
        )
        return result.text  # type: ignore[attr-defined]

    @mcp.tool
    async def sample_with_image(image_bytes: bytes, context: Context) -> str:
        image = Image(data=image_bytes)

        result = await context.sample(
            [
                SamplingMessage(
                    content=TextContent(type="text", text="What's in this image?"),
                    role="user",
                ),
                SamplingMessage(
                    content=image.to_image_content(),
                    role="user",
                ),
            ]
        )
        return result.text  # type: ignore[attr-defined]

    return mcp


async def test_simple_sampling(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        return "This is the sample message!"

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        result = await client.call_tool("simple_sample", {"message": "Hello, world!"})
        assert result.data == "This is the sample message!"


async def test_sampling_with_system_prompt(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert params.systemPrompt is not None
        return params.systemPrompt

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        result = await client.call_tool(
            "sample_with_system_prompt", {"message": "Hello, world!"}
        )
        assert result.data == "You love FastMCP"


async def test_sampling_with_messages(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert len(messages) == 2

        assert isinstance(messages[0].content, TextContent)
        assert messages[0].content.type == "text"
        assert messages[0].content.text == "Hello!"

        assert isinstance(messages[1].content, TextContent)
        assert messages[1].content.type == "text"
        assert messages[1].content.text == "How can I assist you today?"
        return "I need to think."

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        result = await client.call_tool(
            "sample_with_messages", {"message": "Hello, world!"}
        )
        assert result.data == "I need to think."


async def test_sampling_with_fallback(fastmcp_server: FastMCP):
    openai_sampling_handler = AsyncMock(return_value="But I need to think")

    fastmcp_server = FastMCP(
        sampling_handler=openai_sampling_handler,
    )

    @fastmcp_server.tool
    async def sample_with_fallback(context: Context) -> str:
        sampling_result = await context.sample("Do not think.")
        return cast(TextContent, sampling_result).text

    client = Client(fastmcp_server)

    async with client:
        call_tool_result = await client.call_tool("sample_with_fallback")

    assert call_tool_result.data == "But I need to think"


async def test_sampling_with_image(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert len(messages) == 2
        return to_json(messages).decode()

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        image_bytes = b"abc123"
        result = await client.call_tool(
            "sample_with_image", {"image_bytes": image_bytes}
        )
        assert json.loads(result.data) == [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "What's in this image?",
                    "annotations": None,
                    "_meta": None,
                },
                "_meta": None,
            },
            {
                "role": "user",
                "content": {
                    "type": "image",
                    "data": "YWJjMTIz",
                    "mimeType": "image/png",
                    "annotations": None,
                    "_meta": None,
                },
                "_meta": None,
            },
        ]
