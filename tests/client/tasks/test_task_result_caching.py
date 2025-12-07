"""
Tests for Task result caching behavior.

Verifies that Task.result() and await task cache results properly to avoid
redundant server calls and ensure consistent object identity.
"""

from fastmcp import FastMCP
from fastmcp.client import Client


async def test_tool_task_result_cached_on_first_call():
    """First call caches result, subsequent calls return cached value."""
    call_count = 0
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def counting_tool() -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    async with Client(mcp) as client:
        task = await client.call_tool("counting_tool", task=True)

        result1 = await task.result()
        result2 = await task.result()
        result3 = await task.result()

        # All should return 1 (first execution value)
        assert result1.data == 1
        assert result2.data == 1
        assert result3.data == 1

        # Verify they're the same object (cached)
        assert result1 is result2 is result3


async def test_prompt_task_result_cached():
    """PromptTask caches results on first call."""
    call_count = 0
    mcp = FastMCP("test")

    @mcp.prompt(task=True)
    async def counting_prompt() -> str:
        nonlocal call_count
        call_count += 1
        return f"Call number: {call_count}"

    async with Client(mcp) as client:
        task = await client.get_prompt("counting_prompt", task=True)

        result1 = await task.result()
        result2 = await task.result()
        result3 = await task.result()

        # All should return same content
        assert result1.messages[0].content.text == "Call number: 1"
        assert result2.messages[0].content.text == "Call number: 1"
        assert result3.messages[0].content.text == "Call number: 1"

        # Verify they're the same object (cached)
        assert result1 is result2 is result3


async def test_resource_task_result_cached():
    """ResourceTask caches results on first call."""
    call_count = 0
    mcp = FastMCP("test")

    @mcp.resource("file://counter.txt", task=True)
    async def counting_resource() -> str:
        nonlocal call_count
        call_count += 1
        return f"Count: {call_count}"

    async with Client(mcp) as client:
        task = await client.read_resource("file://counter.txt", task=True)

        result1 = await task.result()
        result2 = await task.result()
        result3 = await task.result()

        # All should return same content
        assert result1[0].text == "Count: 1"
        assert result2[0].text == "Count: 1"
        assert result3[0].text == "Count: 1"

        # Verify they're the same object (cached)
        assert result1 is result2 is result3


async def test_multiple_await_returns_same_object():
    """Multiple await task calls return identical object."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def sample_tool() -> str:
        return "result"

    async with Client(mcp) as client:
        task = await client.call_tool("sample_tool", task=True)

        result1 = await task
        result2 = await task
        result3 = await task

        # Should be exact same object in memory
        assert result1 is result2 is result3
        assert id(result1) == id(result2) == id(result3)


async def test_result_and_await_share_cache():
    """task.result() and await task share the same cache."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def sample_tool() -> str:
        return "cached"

    async with Client(mcp) as client:
        task = await client.call_tool("sample_tool", task=True)

        # Call result() first
        result_via_method = await task.result()

        # Then await directly
        result_via_await = await task

        # Should be the same cached object
        assert result_via_method is result_via_await
        assert id(result_via_method) == id(result_via_await)


async def test_forbidden_mode_tool_caches_error_result():
    """Tools with task=False (mode=forbidden) cache error results."""
    mcp = FastMCP("test")

    @mcp.tool(task=False)
    async def non_task_tool() -> int:
        return 1

    async with Client(mcp) as client:
        # Request as task, but mode="forbidden" will reject with error
        task = await client.call_tool("non_task_tool", task=True)

        # Should be immediate (error returned immediately)
        assert task.returned_immediately

        result1 = await task.result()
        result2 = await task.result()
        result3 = await task.result()

        # All should return cached error
        assert result1.is_error
        assert "does not support task-augmented execution" in str(result1)

        # Verify they're the same object (cached)
        assert result1 is result2 is result3


async def test_forbidden_mode_prompt_raises_error():
    """Prompts with task=False (mode=forbidden) raise error."""
    import pytest
    from mcp.shared.exceptions import McpError

    mcp = FastMCP("test")

    @mcp.prompt(task=False)
    async def non_task_prompt() -> str:
        return "Immediate"

    async with Client(mcp) as client:
        # Prompts with mode="forbidden" raise McpError when called with task=True
        with pytest.raises(McpError):
            await client.get_prompt("non_task_prompt", task=True)


async def test_forbidden_mode_resource_raises_error():
    """Resources with task=False (mode=forbidden) raise error."""
    import pytest
    from mcp.shared.exceptions import McpError

    mcp = FastMCP("test")

    @mcp.resource("file://immediate.txt", task=False)
    async def non_task_resource() -> str:
        return "Immediate"

    async with Client(mcp) as client:
        # Resources with mode="forbidden" raise McpError when called with task=True
        with pytest.raises(McpError):
            await client.read_resource("file://immediate.txt", task=True)


async def test_immediate_task_caches_result():
    """Immediate tasks (optional mode called without background) cache results."""
    call_count = 0
    mcp = FastMCP("test", tasks=True)

    # Tool with task=True (optional mode) - but without docket will execute immediately
    @mcp.tool(task=True)
    async def task_tool() -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    async with Client(mcp) as client:
        # Call with task=True
        task = await client.call_tool("task_tool", task=True)

        # Get result multiple times
        result1 = await task.result()
        result2 = await task.result()
        result3 = await task.result()

        # All should return cached value
        assert result1.data == 1
        assert result2.data == 1
        assert result3.data == 1

        # Verify they're the same object (cached)
        assert result1 is result2 is result3


async def test_cache_persists_across_mixed_access_patterns():
    """Cache works correctly when mixing result() and await."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def mixed_tool() -> str:
        return "mixed"

    async with Client(mcp) as client:
        task = await client.call_tool("mixed_tool", task=True)

        # Access in various orders
        result1 = await task
        result2 = await task.result()
        result3 = await task
        result4 = await task.result()

        # All should be the same cached object
        assert result1 is result2 is result3 is result4


async def test_different_tasks_have_separate_caches():
    """Different task instances maintain separate caches."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def separate_tool(value: str) -> str:
        return f"Result: {value}"

    async with Client(mcp) as client:
        task1 = await client.call_tool("separate_tool", {"value": "A"}, task=True)
        task2 = await client.call_tool("separate_tool", {"value": "B"}, task=True)

        result1 = await task1.result()
        result2 = await task2.result()

        # Different results
        assert result1.data == "Result: A"
        assert result2.data == "Result: B"

        # Not the same object
        assert result1 is not result2

        # But each task's cache works independently
        result1_again = await task1.result()
        result2_again = await task2.result()

        assert result1 is result1_again
        assert result2 is result2_again


async def test_cache_survives_status_checks():
    """Calling status() doesn't affect result caching."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def status_check_tool() -> str:
        return "status"

    async with Client(mcp) as client:
        task = await client.call_tool("status_check_tool", task=True)

        # Check status multiple times
        await task.status()
        await task.status()

        result1 = await task.result()

        # Check status again
        await task.status()

        result2 = await task.result()

        # Cache should still work
        assert result1 is result2


async def test_cache_survives_wait_calls():
    """Calling wait() doesn't affect result caching."""
    mcp = FastMCP("test")

    @mcp.tool(task=True)
    async def wait_test_tool() -> str:
        return "waited"

    async with Client(mcp) as client:
        task = await client.call_tool("wait_test_tool", task=True)

        # Wait for completion
        await task.wait()

        result1 = await task.result()

        # Wait again (no-op since completed)
        await task.wait()

        result2 = await task.result()

        # Cache should still work
        assert result1 is result2
