from typing import cast

import pytest
from mcp.types import TextContent

from fastmcp import Client, Context, FastMCP
from fastmcp.client.sampling import RequestContext, SamplingMessage, SamplingParams


@pytest.fixture
def fastmcp_server():
    mcp = FastMCP()

    @mcp.tool
    async def simple_sample(message: str, context: Context) -> str:
        result = await context.sample("Hello, world!")
        return cast(TextContent, result).text

    @mcp.tool
    async def sample_with_system_prompt(message: str, context: Context) -> str:
        result = await context.sample("Hello, world!", system_prompt="You love FastMCP")
        return cast(TextContent, result).text

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
        return cast(TextContent, result).text

    return mcp


async def test_simple_sampling(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        return "This is the sample message!"

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        result = await client.call_tool("simple_sample", {"message": "Hello, world!"})
        reply = cast(TextContent, result[0])
        assert reply.text == "This is the sample message!"


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
        reply = cast(TextContent, result[0])
        assert reply.text == "You love FastMCP"


async def test_sampling_with_messages(fastmcp_server: FastMCP):
    def sampling_handler(
        messages: list[SamplingMessage], params: SamplingParams, ctx: RequestContext
    ) -> str:
        assert len(messages) == 2
        assert messages[0].content.type == "text"
        assert messages[0].content.text == "Hello!"
        assert messages[1].content.type == "text"
        assert messages[1].content.text == "How can I assist you today?"
        return "I need to think."

    async with Client(fastmcp_server, sampling_handler=sampling_handler) as client:
        result = await client.call_tool(
            "sample_with_messages", {"message": "Hello, world!"}
        )
        reply = cast(TextContent, result[0])
        assert reply.text == "I need to think."
