"""
Tests for client-side prompt task methods.

Tests the client's get_prompt_as_task method.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.tasks import PromptTask


@pytest.fixture
async def prompt_server():
    """Create a test server with background-enabled prompts."""
    mcp = FastMCP("prompt-client-test")

    @mcp.prompt(task=True)
    async def analysis_prompt(topic: str, style: str = "formal") -> str:
        """Generate an analysis prompt."""
        return f"Analyze {topic} in a {style} style"

    @mcp.prompt(task=True)
    async def creative_prompt(theme: str) -> str:
        """Generate a creative writing prompt."""
        return f"Write a story about {theme}"

    return mcp


async def test_get_prompt_as_task_returns_prompt_task(prompt_server):
    """get_prompt with task=True returns a PromptTask object."""
    async with Client(prompt_server) as client:
        task = await client.get_prompt("analysis_prompt", {"topic": "AI"}, task=True)

        assert isinstance(task, PromptTask)
        assert isinstance(task.task_id, str)


async def test_prompt_task_server_generated_id(prompt_server):
    """get_prompt with task=True gets server-generated task ID."""
    async with Client(prompt_server) as client:
        task = await client.get_prompt(
            "creative_prompt",
            {"theme": "future"},
            task=True,
        )

        # Server should generate a UUID task ID
        assert task.task_id is not None
        assert isinstance(task.task_id, str)
        # UUIDs have hyphens
        assert "-" in task.task_id


async def test_prompt_task_result_returns_get_prompt_result(prompt_server):
    """PromptTask.result() returns GetPromptResult."""
    async with Client(prompt_server) as client:
        task = await client.get_prompt(
            "analysis_prompt", {"topic": "Robotics", "style": "casual"}, task=True
        )

        # Verify background execution
        assert not task.returned_immediately

        # Get result
        result = await task.result()

        # Result should be GetPromptResult
        assert hasattr(result, "description")
        assert hasattr(result, "messages")
        # Check the rendered message content, not the description
        assert len(result.messages) > 0
        assert "Analyze Robotics" in result.messages[0].content.text


async def test_prompt_task_await_syntax(prompt_server):
    """PromptTask can be awaited directly."""
    async with Client(prompt_server) as client:
        task = await client.get_prompt("creative_prompt", {"theme": "ocean"}, task=True)

        # Can await task directly
        result = await task
        assert "Write a story about ocean" in result.messages[0].content.text


async def test_prompt_task_status_and_wait(prompt_server):
    """PromptTask supports status() and wait() methods."""
    async with Client(prompt_server) as client:
        task = await client.get_prompt("analysis_prompt", {"topic": "Space"}, task=True)

        # Check status
        status = await task.status()
        assert status.status in ["working", "completed"]

        # Wait for completion
        await task.wait(timeout=2.0)

        # Get result
        result = await task.result()
        assert "Analyze Space" in result.messages[0].content.text
