"""
Tests that synchronous functions cannot be used as background tasks.

Docket requires async functions for background execution. FastMCP automatically
disables task support for sync functions, with warnings for explicit task=True.
"""

from pytest import LogCaptureFixture

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.utilities.tests import caplog_for_fastmcp


async def test_sync_tool_with_explicit_task_true_warns_and_disables(
    caplog: LogCaptureFixture,
):
    """Sync tool with task=True logs warning and disables task support."""
    import logging

    with caplog_for_fastmcp(caplog):
        caplog.set_level(logging.INFO)

        mcp = FastMCP("test")

        @mcp.tool(task=True)
        def sync_tool(x: int) -> int:
            """A synchronous tool."""
            logging.getLogger("fastmcp.myserver").info("I came from the tool!")
            return x * 2

        # Should have logged a warning during decoration
        assert "task=True but is synchronous" in caplog.text
        assert "Disabling task support" in caplog.text

        # Tool should have task=False after being disabled
        tool = await mcp.get_tool("sync_tool")
        assert tool.task is False

        # Verify execution: even if client requests task=True, should execute immediately
        async with Client(mcp) as client:
            task = await client.call_tool("sync_tool", {"x": 5}, task=True)
            assert task.returned_immediately
            result = await task.result()
            assert result.data == 10

        # Should have seen the log from inside the function
        assert "I came from the tool!" in caplog.text


async def test_sync_tool_with_inherited_task_true_quietly_disables(
    caplog: LogCaptureFixture,
):
    """Sync tool inheriting task=True from server disables quietly (no warning)."""
    import logging

    with caplog_for_fastmcp(caplog):
        caplog.set_level(logging.INFO)

        mcp = FastMCP("test", tasks=True)

        @mcp.tool()  # Inherits task=True from server
        def sync_tool(x: int) -> int:
            """A synchronous tool."""
            logging.getLogger("fastmcp.myserver").info("I came from the tool!")
            return x * 2

        # Should NOT have logged a warning (quietly disabled)
        assert "task=True but is synchronous" not in caplog.text

        # Tool should have task=False after being disabled
        tool = await mcp.get_tool("sync_tool")
        assert tool.task is False

        # Verify execution: should execute immediately
        async with Client(mcp) as client:
            task = await client.call_tool("sync_tool", {"x": 3}, task=True)
            assert task.returned_immediately
            result = await task.result()
            assert result.data == 6

        # Should have seen the log from inside the function
        assert "I came from the tool!" in caplog.text


async def test_sync_prompt_with_explicit_task_true_warns_and_disables(
    caplog: LogCaptureFixture,
):
    """Sync prompt with task=True logs warning and disables task support."""
    import logging

    with caplog_for_fastmcp(caplog):
        caplog.set_level(logging.INFO)

        mcp = FastMCP("test")

        @mcp.prompt(task=True)
        def sync_prompt() -> str:
            """A synchronous prompt."""
            logging.getLogger("fastmcp.myserver").info("I came from the prompt!")
            return "Hello"

        # Should have logged a warning during decoration
        assert "task=True but is synchronous" in caplog.text
        assert "Disabling task support" in caplog.text

        # Prompt should have task=False
        prompt = await mcp.get_prompt("sync_prompt")
        assert prompt.task is False

        # Verify execution: should execute immediately
        async with Client(mcp) as client:
            task = await client.get_prompt("sync_prompt", task=True)
            assert task.returned_immediately
            result = await task.result()
            assert "Hello" in str(result)

        # Should have seen the log from inside the function
        assert "I came from the prompt!" in caplog.text


async def test_sync_prompt_with_inherited_task_true_quietly_disables(
    caplog: LogCaptureFixture,
):
    """Sync prompt inheriting task=True disables quietly."""
    import logging

    with caplog_for_fastmcp(caplog):
        caplog.set_level(logging.INFO)

        mcp = FastMCP("test", tasks=True)

        @mcp.prompt()  # Inherits task=True from server
        def sync_prompt() -> str:
            """A synchronous prompt."""
            logging.getLogger("fastmcp.myserver").info("I came from the prompt!")
            return "Hello"

        # Should NOT have logged a warning (quietly disabled)
        assert "task=True but is synchronous" not in caplog.text

        # Prompt should have task=False
        prompt = await mcp.get_prompt("sync_prompt")
        assert prompt.task is False

        # Verify execution: should execute immediately
        async with Client(mcp) as client:
            task = await client.get_prompt("sync_prompt", task=True)
            assert task.returned_immediately
            result = await task.result()
            assert "Hello" in str(result)

        # Should have seen the log from inside the function
        assert "I came from the prompt!" in caplog.text


async def test_sync_resource_with_explicit_task_true_warns_and_disables(
    caplog: LogCaptureFixture,
):
    """Sync resource with task=True logs warning and disables task support."""
    import logging

    with caplog_for_fastmcp(caplog):
        caplog.set_level(logging.INFO)

        mcp = FastMCP("test")

        @mcp.resource("test://sync", task=True)
        def sync_resource() -> str:
            """A synchronous resource."""
            logging.getLogger("fastmcp.myserver").info("I came from the resource!")
            return "data"

        # Should have logged a warning during decoration
        assert "task=True but is synchronous" in caplog.text
        assert "Disabling task support" in caplog.text

        # Resource should have task=False
        resource = await mcp._resource_manager.get_resource("test://sync")
        assert resource.task is False

        # Verify execution: should execute immediately
        async with Client(mcp) as client:
            task = await client.read_resource("test://sync", task=True)
            assert task.returned_immediately
            result = await task.result()
            assert "data" in str(result)

        # Should have seen the log from inside the function
        assert "I came from the resource!" in caplog.text


async def test_sync_resource_with_inherited_task_true_quietly_disables(
    caplog: LogCaptureFixture,
):
    """Sync resource inheriting task=True disables quietly."""
    import logging

    with caplog_for_fastmcp(caplog):
        caplog.set_level(logging.INFO)

        mcp = FastMCP("test", tasks=True)

        @mcp.resource("test://sync")  # Inherits task=True from server
        def sync_resource() -> str:
            """A synchronous resource."""
            logging.getLogger("fastmcp.myserver").info("I came from the resource!")
            return "data"

        # Should NOT have logged a warning (quietly disabled)
        assert "task=True but is synchronous" not in caplog.text

        # Resource should have task=False
        resource = await mcp._resource_manager.get_resource("test://sync")
        assert resource.task is False

        # Verify execution: should execute immediately
        async with Client(mcp) as client:
            task = await client.read_resource("test://sync", task=True)
            assert task.returned_immediately
            result = await task.result()
            assert "data" in str(result)

        # Should have seen the log from inside the function
        assert "I came from the resource!" in caplog.text


async def test_async_tool_with_task_true_remains_enabled():
    """Async tools with task=True keep task support enabled."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def async_tool(x: int) -> int:
        """An async tool."""
        return x * 2

    # Tool should have task=True
    tool = await mcp.get_tool("async_tool")
    assert tool.task is True


async def test_async_prompt_with_task_true_remains_enabled():
    """Async prompts with task=True keep task support enabled."""
    mcp = FastMCP("test")

    @mcp.prompt(task=True)
    async def async_prompt() -> str:
        """An async prompt."""
        return "Hello"

    # Prompt should have task=True
    prompt = await mcp.get_prompt("async_prompt")
    assert prompt.task is True


async def test_async_resource_with_task_true_remains_enabled():
    """Async resources with task=True keep task support enabled."""
    mcp = FastMCP("test")

    @mcp.resource("test://async", task=True)
    async def async_resource() -> str:
        """An async resource."""
        return "data"

    # Resource should have task=True
    resource = await mcp._resource_manager.get_resource("test://async")
    assert resource.task is True
