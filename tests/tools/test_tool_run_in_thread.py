"""Tests for the run_in_thread flag on sync tools.

Sync tools default to running on a worker thread so they don't block the
event loop. ``run_in_thread=False`` opts out and runs them inline on the
event loop thread — useful for libraries with thread affinity (Windows
COM, tkinter, etc.).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest
from mcp.types import TextContent

from fastmcp import FastMCP
from fastmcp.tools.base import Tool


async def _loop_thread_id() -> int:
    return threading.get_ident()


class TestRunInThread:
    async def test_sync_default_runs_in_worker_thread(self):
        """Default sync dispatch runs on a thread distinct from the loop's."""
        mcp = FastMCP()
        loop_tid = await _loop_thread_id()

        @mcp.tool
        def where_am_i() -> int:
            return threading.get_ident()

        result = await mcp.call_tool("where_am_i")
        assert result.structured_content is not None
        tid = result.structured_content["result"]
        assert tid != loop_tid

    async def test_sync_run_in_thread_false_runs_on_loop_thread(self):
        """run_in_thread=False runs the sync fn on the event loop thread."""
        mcp = FastMCP()
        loop_tid = await _loop_thread_id()

        @mcp.tool(run_in_thread=False)
        def where_am_i() -> int:
            return threading.get_ident()

        result = await mcp.call_tool("where_am_i")
        assert result.structured_content is not None
        assert result.structured_content["result"] == loop_tid

    def test_sync_run_in_thread_false_rejects_timeout(self):
        """Combining timeout with run_in_thread=False on a sync fn is rejected.

        Inline execution has no cancellation checkpoints, so anyio.fail_after
        cannot preempt the call — accepting the combination would silently
        render the timeout a no-op. We force users to make an explicit choice.
        """
        mcp = FastMCP()

        with pytest.raises(ValueError, match="timeout cannot be enforced"):

            @mcp.tool(run_in_thread=False, timeout=5.0)
            def blocked() -> str:
                return "unreachable"

    async def test_async_tool_allows_timeout_and_run_in_thread_false(self):
        """run_in_thread is a no-op for async fns, so pairing with timeout is fine."""
        mcp = FastMCP()

        @mcp.tool(run_in_thread=False, timeout=5.0)
        async def ok() -> str:
            return "ok"

        result = await mcp.call_tool("ok")
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "ok"

    def test_async_generator_allowed_with_timeout_and_run_in_thread_false(self):
        """Async generators are async even though is_coroutine_function is False.

        Registration must not over-block this shape — the generator's
        iteration has await points, so timeout enforcement still works.
        """
        from fastmcp.tools.base import Tool

        async def stream() -> AsyncIterator[str]:
            yield "a"
            yield "b"

        # Must not raise.
        Tool.from_function(stream, timeout=5.0, run_in_thread=False)

    async def test_async_tool_unaffected_by_run_in_thread_flag(self):
        """The flag is a no-op for async tools (they already run on the loop)."""
        mcp = FastMCP()
        loop_tid = await _loop_thread_id()

        @mcp.tool(run_in_thread=False)
        async def where_am_i() -> int:
            return threading.get_ident()

        result = await mcp.call_tool("where_am_i")
        assert result.structured_content is not None
        assert result.structured_content["result"] == loop_tid

    async def test_run_in_thread_false_blocks_other_tasks(self):
        """A sync tool with run_in_thread=False blocks the event loop.

        This documents the tradeoff: while the tool runs inline, no other
        task on the loop makes progress. Contrast with the default path,
        where the sync call is offloaded and concurrent tasks continue.
        """
        mcp = FastMCP()

        @mcp.tool(run_in_thread=False)
        def blocking() -> str:
            import time

            time.sleep(0.2)
            return "done"

        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.create_task(tick())
        try:
            result = await mcp.call_tool("blocking")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "done"
        # With inline execution, ticks should be near zero — the 200ms sleep
        # blocks the loop. Under a thread pool (default), ticks would be ~10.
        assert ticks <= 2

    async def test_default_threadpool_permits_concurrency(self):
        """Sanity check: the default path does not block the loop."""
        mcp = FastMCP()

        @mcp.tool
        def blocking() -> str:
            import time

            time.sleep(0.2)
            return "done"

        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.create_task(tick())
        try:
            result = await mcp.call_tool("blocking")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "done"
        assert ticks >= 5


class TestRunInThreadViaStandaloneDecorator:
    async def test_standalone_tool_decorator_accepts_run_in_thread(self):
        from fastmcp.tools.function_tool import tool as tool_decorator

        @tool_decorator(run_in_thread=False)
        def fn() -> int:
            return threading.get_ident()

        mcp = FastMCP()
        mcp.add_tool(fn)

        loop_tid = await _loop_thread_id()
        result = await mcp.call_tool("fn")
        assert result.structured_content is not None
        assert result.structured_content["result"] == loop_tid


class TestRunInThreadViaFileSystemProvider:
    async def test_filesystem_provider_respects_run_in_thread(self, tmp_path):
        """Tools discovered by FileSystemProvider honor run_in_thread=False.

        FileSystemProvider extends LocalProvider and registers filesystem-
        discovered tools via add_tool(), which reads ToolMeta.run_in_thread
        attached by the standalone @tool decorator.
        """
        from fastmcp.server.providers import FileSystemProvider

        (tmp_path / "where.py").write_text(
            "import threading\n"
            "from fastmcp.tools import tool\n\n"
            "@tool(run_in_thread=False)\n"
            "def where_am_i() -> int:\n"
            "    return threading.get_ident()\n"
        )

        provider = FileSystemProvider(tmp_path)
        mcp = FastMCP(providers=[provider])

        loop_tid = await _loop_thread_id()
        result = await mcp.call_tool("where_am_i")
        assert result.structured_content is not None
        assert result.structured_content["result"] == loop_tid

    async def test_filesystem_provider_forwards_timeout(self, tmp_path):
        """Filesystem discovery forwards `timeout` from ToolMeta.

        Previously dropped, which let sync tools discovered via the
        filesystem bypass both timeout enforcement and the registration-time
        guard against combining timeout with run_in_thread=False.
        """
        from fastmcp.server.providers import FileSystemProvider

        (tmp_path / "t.py").write_text(
            "from fastmcp.tools import tool\n\n"
            "@tool(timeout=5.0)\n"
            "def quick() -> str:\n"
            "    return 'ok'\n"
        )

        provider = FileSystemProvider(tmp_path)
        discovered = [
            c
            for c in provider._components.values()
            if isinstance(c, Tool) and c.name == "quick"
        ]
        assert len(discovered) == 1
        assert discovered[0].timeout == 5.0
