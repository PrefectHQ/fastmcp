from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError


class TestToolHooks:
    async def test_setup_runs_before_tool(self):
        mcp = FastMCP()
        events: list[str] = []

        def setup_hook() -> None:
            events.append("setup")

        @mcp.tool(setup=setup_hook)
        def my_tool() -> str:
            events.append("tool")
            return "ok"

        result = await mcp.call_tool("my_tool")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"
        assert events == ["setup", "tool"]

    async def test_teardown_runs_after_successful_completion(self):
        mcp = FastMCP()
        events: list[str] = []

        def teardown_hook() -> None:
            events.append("teardown")

        @mcp.tool(teardown=teardown_hook)
        def my_tool() -> str:
            events.append("tool")
            return "ok"

        result = await mcp.call_tool("my_tool")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"
        assert events == ["tool", "teardown"]

    async def test_teardown_receives_raw_result(self):
        mcp = FastMCP()
        received_result: Any = None

        def teardown_hook(result: Any) -> None:
            nonlocal received_result
            received_result = result

        @mcp.tool(teardown=teardown_hook)
        def my_tool() -> dict[str, str]:
            return {"status": "ok"}

        result = await mcp.call_tool("my_tool")

        assert result.structured_content == {"status": "ok"}
        assert received_result == {"status": "ok"}

    async def test_teardown_receives_exception_on_failure(self):
        mcp = FastMCP()
        received_exception: BaseException | None = None

        def teardown_hook(exception: BaseException | None) -> None:
            nonlocal received_exception
            received_exception = exception

        @mcp.tool(teardown=teardown_hook)
        def my_tool() -> str:
            raise ValueError("Simulated failure")

        with pytest.raises(ToolError, match="Simulated failure") as exc_info:
            await mcp.call_tool("my_tool")

        assert isinstance(exc_info.value.__cause__, ValueError)
        assert isinstance(received_exception, ValueError)

    async def test_teardown_runs_on_timeout(self):
        mcp = FastMCP()
        teardown_called = False
        received_timed_out: bool | None = None

        def teardown_hook(timed_out: bool) -> None:
            nonlocal teardown_called, received_timed_out
            teardown_called = True
            received_timed_out = timed_out

        @mcp.tool(teardown=teardown_hook, timeout=0.1)
        async def my_tool() -> str:
            await asyncio.sleep(0.2)
            return "ok"

        with pytest.raises(ToolError):
            await mcp.call_tool("my_tool")

        assert teardown_called
        assert received_timed_out is True

    async def test_teardown_runs_on_asyncio_cancelled_error(self):
        mcp = FastMCP()
        tool_started = asyncio.Event()
        teardown_finished = asyncio.Event()
        received_exception: BaseException | None = None

        async def teardown_hook(exception: BaseException | None) -> None:
            nonlocal received_exception
            received_exception = exception
            await asyncio.sleep(0)
            teardown_finished.set()

        @mcp.tool(teardown=teardown_hook)
        async def my_tool() -> str:
            tool_started.set()
            await asyncio.Event().wait()
            return "ok"

        task = asyncio.create_task(mcp.call_tool("my_tool"))
        await tool_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert teardown_finished.is_set()
        assert isinstance(received_exception, asyncio.CancelledError)

    async def test_teardown_receives_setup_result(self):
        mcp = FastMCP()
        received_setup_result: str | None = None

        def setup_hook() -> str:
            return "resource"

        def teardown_hook(setup_result: str | None) -> None:
            nonlocal received_setup_result
            received_setup_result = setup_result

        @mcp.tool(setup=setup_hook, teardown=teardown_hook)
        def my_tool() -> str:
            return "ok"

        await mcp.call_tool("my_tool")

        assert received_setup_result == "resource"

    async def test_teardown_runs_when_setup_fails(self):
        mcp = FastMCP()
        events: list[str] = []
        received_exception: BaseException | None = None
        received_setup_result: Any = "unset"

        def setup_hook() -> str:
            events.append("setup")
            raise RuntimeError("Setup failed")

        def teardown_hook(
            exception: BaseException | None, setup_result: Any = None
        ) -> None:
            nonlocal received_exception, received_setup_result
            events.append("teardown")
            received_exception = exception
            received_setup_result = setup_result

        @mcp.tool(setup=setup_hook, teardown=teardown_hook)
        def my_tool() -> str:
            events.append("tool")
            return "ok"

        with pytest.raises(ToolError, match="Setup failed") as exc_info:
            await mcp.call_tool("my_tool")

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert isinstance(received_exception, RuntimeError)
        assert received_setup_result is None
        assert events == ["setup", "teardown"]

    async def test_sync_tool_with_sync_hooks(self):
        mcp = FastMCP()
        events: list[str] = []

        def setup_hook() -> None:
            events.append("setup")

        def teardown_hook() -> None:
            events.append("teardown")

        @mcp.tool(setup=setup_hook, teardown=teardown_hook)
        def my_tool() -> str:
            events.append("tool")
            return "ok"

        result = await mcp.call_tool("my_tool")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"
        assert events == ["setup", "tool", "teardown"]

    async def test_sync_tool_with_async_hooks(self):
        mcp = FastMCP()
        events: list[str] = []

        async def setup_hook() -> None:
            events.append("setup")

        async def teardown_hook() -> None:
            events.append("teardown")

        @mcp.tool(setup=setup_hook, teardown=teardown_hook)
        def my_tool() -> str:
            events.append("tool")
            return "ok"

        result = await mcp.call_tool("my_tool")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"
        assert events == ["setup", "tool", "teardown"]

    async def test_async_tool_with_sync_hooks(self):
        mcp = FastMCP()
        events: list[str] = []

        def setup_hook() -> None:
            events.append("setup")

        def teardown_hook() -> None:
            events.append("teardown")

        @mcp.tool(setup=setup_hook, teardown=teardown_hook)
        async def my_tool() -> str:
            events.append("tool")
            return "ok"

        result = await mcp.call_tool("my_tool")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"
        assert events == ["setup", "tool", "teardown"]

    async def test_async_tool_with_async_hooks(self):
        mcp = FastMCP()
        events: list[str] = []

        async def setup_hook() -> None:
            events.append("setup")

        async def teardown_hook() -> None:
            events.append("teardown")

        @mcp.tool(setup=setup_hook, teardown=teardown_hook)
        async def my_tool() -> str:
            events.append("tool")
            return "ok"

        result = await mcp.call_tool("my_tool")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"
        assert events == ["setup", "tool", "teardown"]

    async def test_teardown_failure_preserves_original_tool_exception(self):
        mcp = FastMCP()

        def teardown_hook() -> None:
            raise RuntimeError("Teardown failed")

        @mcp.tool(teardown=teardown_hook)
        def my_tool() -> str:
            raise ValueError("Original tool failure")

        with pytest.raises(ToolError, match="Original tool failure") as exc_info:
            await mcp.call_tool("my_tool")

        assert isinstance(exc_info.value.__cause__, ValueError)

    async def test_teardown_failure_after_success_preserves_tool_result(self):
        mcp = FastMCP()

        def teardown_hook() -> None:
            raise RuntimeError("Teardown failed")

        @mcp.tool(teardown=teardown_hook)
        def my_tool() -> str:
            return "ok"

        result = await mcp.call_tool("my_tool")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"

    async def test_no_arg_teardown_works(self):
        mcp = FastMCP()
        teardown_called = False

        def teardown_hook() -> None:
            nonlocal teardown_called
            teardown_called = True

        @mcp.tool(teardown=teardown_hook)
        def my_tool() -> str:
            return "ok"

        result = await mcp.call_tool("my_tool")

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"
        assert teardown_called
