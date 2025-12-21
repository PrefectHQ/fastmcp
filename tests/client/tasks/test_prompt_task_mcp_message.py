"""Test prompt task with mcp.types.PromptMessage return values.

Regression test for: PromptMessage object has no attribute 'to_mcp'
"""

import mcp.types
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.client import Client


async def test_prompt_task_with_mcp_prompt_message():
    """Prompt task returning mcp.types.PromptMessage should serialize correctly."""
    mcp_server = FastMCP("test")

    @mcp_server.prompt(task=True)
    async def greeting(name: str) -> list[mcp.types.PromptMessage]:
        return [
            mcp.types.PromptMessage(
                role="user",
                content=mcp.types.TextContent(type="text", text=f"Hello {name}"),
            )
        ]

    async with Client(mcp_server) as client:
        task = await client.get_prompt("greeting", {"name": "World"}, task=True)
        result = await task.result()
        assert isinstance(result.messages[0].content, TextContent)
        assert "Hello World" in result.messages[0].content.text


async def test_prompt_task_with_multiple_mcp_prompt_messages():
    """Prompt task returning multiple mcp.types.PromptMessage objects."""
    mcp_server = FastMCP("test")

    @mcp_server.prompt(task=True)
    async def conversation(topic: str) -> list[mcp.types.PromptMessage]:
        return [
            mcp.types.PromptMessage(
                role="user",
                content=mcp.types.TextContent(
                    type="text", text=f"Tell me about {topic}"
                ),
            ),
            mcp.types.PromptMessage(
                role="assistant",
                content=mcp.types.TextContent(
                    type="text", text=f"{topic} is fascinating!"
                ),
            ),
        ]

    async with Client(mcp_server) as client:
        task = await client.get_prompt("conversation", {"topic": "space"}, task=True)
        result = await task.result()
        assert len(result.messages) == 2
        assert isinstance(result.messages[0].content, TextContent)
        assert "Tell me about space" in result.messages[0].content.text
        assert isinstance(result.messages[1].content, TextContent)
        assert "space is fascinating" in result.messages[1].content.text
