"""
Tests for SEP-1686 background task support for prompts.

Tests that prompts with task=True can execute in background.
"""

import pytest

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.tasks import PromptTask


@pytest.fixture
async def prompt_server():
    """Create a FastMCP server with task-enabled prompts."""
    mcp = FastMCP("prompt-test-server")

    @mcp.prompt()
    async def simple_prompt(topic: str) -> str:
        """A simple prompt template."""
        return f"Write about: {topic}"

    @mcp.prompt(task=True)
    async def background_prompt(topic: str, depth: str = "detailed") -> str:
        """A prompt that can execute in background."""
        return f"Write a {depth} analysis of: {topic}"

    return mcp


async def test_synchronous_prompt_unchanged(prompt_server):
    """Prompts without task metadata execute synchronously as before."""
    async with Client(prompt_server) as client:
        # Regular call without task metadata
        result = await client.get_prompt("simple_prompt", {"topic": "AI"})

        # Should execute immediately and return result
        assert "Write about: AI" in str(result)


async def test_prompt_with_task_metadata_returns_immediately(prompt_server):
    """Prompts with task metadata return immediately with PromptTask object."""
    async with Client(prompt_server) as client:
        # Call with task metadata
        task = await client.get_prompt("background_prompt", {"topic": "AI"}, task=True)

        # Should return a PromptTask object immediately
        assert isinstance(task, PromptTask)
        assert isinstance(task.task_id, str)
        assert len(task.task_id) > 0


async def test_prompt_task_executes_in_background(prompt_server):
    """Prompt task executes via Docket in background."""
    async with Client(prompt_server) as client:
        task = await client.get_prompt(
            "background_prompt",
            {"topic": "Machine Learning", "depth": "comprehensive"},
            task=True,
        )

        # Verify background execution
        assert not task.returned_immediately

        # Get the result
        result = await task.result()
        assert "comprehensive" in result.messages[0].content.text.lower()


async def test_forbidden_mode_prompt_rejects_task_calls(prompt_server):
    """Prompts with task=False (mode=forbidden) reject task-augmented calls."""
    from mcp.shared.exceptions import McpError
    from mcp.types import METHOD_NOT_FOUND

    @prompt_server.prompt(task=False)  # Explicitly disable task support
    async def sync_only_prompt(topic: str) -> str:
        return f"Sync prompt: {topic}"

    async with Client(prompt_server) as client:
        # Calling with task=True when task=False should raise McpError
        import pytest

        with pytest.raises(McpError) as exc_info:
            await client.get_prompt("sync_only_prompt", {"topic": "test"}, task=True)

        # New behavior: mode="forbidden" returns METHOD_NOT_FOUND error
        assert exc_info.value.error.code == METHOD_NOT_FOUND
        assert (
            "does not support task-augmented execution" in exc_info.value.error.message
        )
