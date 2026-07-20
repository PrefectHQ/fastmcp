import asyncio
import gc
import inspect
import os
import time
import weakref
from pathlib import Path

import psutil
import pytest
from mcp.shared.exceptions import MCPError

from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport, StdioTransport

# A pure-stdlib MCP server used by the process-lifecycle tests below. It starts
# in ~0.03s instead of the ~0.7s a real FastMCP server needs, which matters
# because these tests spawn several subprocesses each. See its docstring for
# what it does and does not implement.
MINIMAL_STDIO_SERVER = Path(__file__).parent / "minimal_stdio_server.py"


def running_under_debugger():
    return os.environ.get("DEBUGPY_RUNNING") == "true"


def gc_collect_harder():
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()


async def wait_for_log_content(
    log_file_path, expected: str, timeout: float = 2.0
) -> str:
    """Poll a log file until it contains the expected text.

    The subprocess's stderr is redirected straight to the file at the OS
    level (no async pump on our side to synchronize on), so poll for the
    content instead of sleeping a fixed amount and hoping it landed.
    """

    async def _poll() -> str:
        while True:
            content = log_file_path.read_text()
            if expected in content:
                return content
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout=timeout)


async def wait_for_process_exit(pid: int | None, timeout: float = 5.0) -> None:
    """Poll until the given pid is gone, failing clearly if it never exits.

    The subprocesses under test self-terminate within a fraction of a second,
    so a bounded poll costs nothing and turns a hung teardown into a named
    failure instead of an opaque suite-level timeout.
    """
    assert pid is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"Subprocess {pid} was still alive after {timeout}s")


class TestDisconnect:
    async def test_cancelled_connection_task_is_cleaned_up(self):
        transport = StdioTransport(command="python", args=[])
        connect_task = asyncio.create_task(asyncio.sleep(0))
        connect_task.cancel()
        transport._connect_task = connect_task

        await transport.disconnect()

        assert transport._connect_task is None
        assert not transport._stop_event.is_set()

    async def test_caller_cancellation_is_not_suppressed(self):
        transport = StdioTransport(command="python", args=[])
        connection_finished = asyncio.Event()
        connect_task = asyncio.create_task(connection_finished.wait())
        transport._connect_task = connect_task

        disconnect_task = asyncio.create_task(transport.disconnect())
        await asyncio.sleep(0)
        disconnect_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await disconnect_task
        assert not connect_task.cancelled()

        connection_finished.set()
        await connect_task
        await transport.disconnect()

    async def test_caller_cancellation_wins_when_connection_is_also_cancelled(self):
        transport = StdioTransport(command="python", args=[])
        connect_task = asyncio.create_task(asyncio.Event().wait())
        transport._connect_task = connect_task

        disconnect_task = asyncio.create_task(transport.disconnect())
        await asyncio.sleep(0)
        connect_task.cancel()
        disconnect_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await disconnect_task


class TestParallelCalls:
    @pytest.fixture
    def stdio_script(self):
        return MINIMAL_STDIO_SERVER

    async def test_parallel_calls(self, stdio_script):
        from fastmcp.server import create_proxy

        backend_transport = PythonStdioTransport(script_path=stdio_script)
        backend_client = Client(transport=backend_transport)

        proxy = create_proxy(backend_client, name="PROXY")

        count = 10

        tasks = [proxy.list_tools() for _ in range(count)]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert len(results) == count
        errors = [result for result in results if isinstance(result, Exception)]
        assert len(errors) == 0


@pytest.mark.timeout(15)
class TestKeepAlive:
    # https://github.com/PrefectHQ/fastmcp/issues/581

    @pytest.fixture
    def stdio_script(self):
        return MINIMAL_STDIO_SERVER

    async def test_keep_alive_default_true(self):
        client = Client(transport=StdioTransport(command="python", args=[""]))

        assert client.transport.keep_alive is True

    async def test_keep_alive_set_false(self):
        client = Client(
            transport=StdioTransport(command="python", args=[""], keep_alive=False)
        )
        assert client.transport.keep_alive is False

    async def test_keep_alive_maintains_session_across_multiple_calls(
        self, stdio_script
    ):
        client = Client(transport=PythonStdioTransport(script_path=stdio_script))
        assert client.transport.keep_alive is True

        async with client:
            result1 = await client.call_tool("pid")
            pid1: int = result1.data

        async with client:
            result2 = await client.call_tool("pid")
            pid2: int = result2.data

        assert pid1 == pid2

    @pytest.mark.skipif(
        running_under_debugger(), reason="Debugger holds a reference to the transport"
    )
    async def test_keep_alive_true_exit_scope_kills_transport(self, stdio_script):
        transport_weak_ref: weakref.ref[PythonStdioTransport] | None = None

        async def test_server():
            transport = PythonStdioTransport(script_path=stdio_script, keep_alive=True)
            nonlocal transport_weak_ref
            transport_weak_ref = weakref.ref(transport)
            async with transport.connect_session():
                pass

        await test_server()

        gc_collect_harder()

        # This test will fail while debugging because the debugger holds a reference to the underlying transport
        assert transport_weak_ref
        transport = transport_weak_ref()
        assert transport is None

    @pytest.mark.skipif(
        running_under_debugger(), reason="Debugger holds a reference to the transport"
    )
    async def test_keep_alive_true_exit_scope_kills_client(self, stdio_script):
        pid: int | None = None

        async def test_server():
            transport = PythonStdioTransport(script_path=stdio_script, keep_alive=True)
            client = Client(transport=transport)

            assert client.transport.keep_alive is True

            async with client:
                result1 = await client.call_tool("pid")
                nonlocal pid
                pid = result1.data

        await test_server()

        gc_collect_harder()

        # This test may fail/hang while debugging because the debugger holds a reference to the underlying transport

        await wait_for_process_exit(pid)

    async def test_keep_alive_false_exit_scope_kills_server(self, stdio_script):
        pid: int | None = None

        async def test_server():
            transport = PythonStdioTransport(script_path=stdio_script, keep_alive=False)
            client = Client(transport=transport)
            assert client.transport.keep_alive is False
            async with client:
                result1 = await client.call_tool("pid")
                nonlocal pid
                pid = result1.data

            del client

        await test_server()

        await wait_for_process_exit(pid)

    async def test_keep_alive_false_starts_new_session_across_multiple_calls(
        self, stdio_script
    ):
        client = Client(
            transport=PythonStdioTransport(script_path=stdio_script, keep_alive=False)
        )
        assert client.transport.keep_alive is False

        async with client:
            result1 = await client.call_tool("pid")
            pid1: int = result1.data

        async with client:
            result2 = await client.call_tool("pid")
            pid2: int = result2.data

        assert pid1 != pid2

    async def test_keep_alive_starts_new_session_if_manually_closed(self, stdio_script):
        client = Client(transport=PythonStdioTransport(script_path=stdio_script))
        assert client.transport.keep_alive is True

        async with client:
            result1 = await client.call_tool("pid")
            pid1: int = result1.data

        await client.close()

        async with client:
            result2 = await client.call_tool("pid")
            pid2: int = result2.data

        assert pid1 != pid2

    async def test_keep_alive_maintains_session_if_reentered(self, stdio_script):
        client = Client(transport=PythonStdioTransport(script_path=stdio_script))
        assert client.transport.keep_alive is True

        async with client:
            result1 = await client.call_tool("pid")
            pid1: int = result1.data

            async with client:
                result2 = await client.call_tool("pid")
                pid2: int = result2.data

            result3 = await client.call_tool("pid")
            pid3: int = result3.data

        assert pid1 == pid2 == pid3

    async def test_close_session_and_try_to_use_client_raises_error(self, stdio_script):
        client = Client(transport=PythonStdioTransport(script_path=stdio_script))
        assert client.transport.keep_alive is True

        async with client:
            await client.close()
            with pytest.raises(RuntimeError, match="Client is not connected"):
                await client.call_tool("pid")

    async def test_session_task_failure_raises_immediately_on_enter(self):
        # Use a command that will fail to start
        client = Client(
            transport=StdioTransport(command="nonexistent_command", args=[])
        )

        # Should raise RuntimeError immediately, not defer until first use
        with pytest.raises(RuntimeError, match="Client failed to connect"):
            async with client:
                pass


@pytest.mark.timeout(15)
class TestSubprocessCrashRecovery:
    """Test that StdioTransport recovers after the subprocess crashes."""

    # Use a short init_timeout so tests fail fast instead of hanging if
    # stream-based dead-session detection is slow (e.g. on Windows where
    # pipe cleanup can lag after process termination).
    INIT_TIMEOUT = 3

    @pytest.fixture
    def stdio_script(self):
        return MINIMAL_STDIO_SERVER

    async def test_keep_alive_recovers_after_subprocess_crash(self, stdio_script):
        """When keep_alive=True and the subprocess dies, the next connection should start a fresh subprocess."""
        transport = PythonStdioTransport(script_path=stdio_script)
        client = Client(transport=transport, init_timeout=self.INIT_TIMEOUT)
        assert transport.keep_alive is True

        # First connection: get the PID of the subprocess
        async with client:
            result1 = await client.call_tool("pid")
            pid1: int = result1.data

        # Kill the subprocess to simulate a crash
        psutil.Process(pid1).kill()

        # First attempt after crash fails — the stale session is
        # detected and torn down so subsequent attempts succeed.
        with pytest.raises(Exception):
            async with client:
                await client.call_tool("pid")

        # Next connection starts a fresh subprocess
        async with client:
            result2 = await client.call_tool("pid")
            pid2: int = result2.data

        assert pid1 != pid2

    async def test_keep_alive_false_recovers_after_subprocess_crash(self, stdio_script):
        """When keep_alive=False, crash recovery works because disconnect() is always called."""
        client = Client(
            transport=PythonStdioTransport(script_path=stdio_script, keep_alive=False),
            init_timeout=self.INIT_TIMEOUT,
        )

        async with client:
            result1 = await client.call_tool("pid")
            pid1: int = result1.data

        # Process should already be dead (keep_alive=False), but kill to be sure
        with pytest.raises(psutil.NoSuchProcess):
            psutil.Process(pid1).kill()

        # Next connection should work fine
        async with client:
            result2 = await client.call_tool("pid")
            pid2: int = result2.data

        assert pid1 != pid2

    async def test_multiple_consecutive_crashes(self, stdio_script):
        """Recovery works across multiple crash/reconnect cycles."""
        client = Client(
            transport=PythonStdioTransport(script_path=stdio_script),
            init_timeout=self.INIT_TIMEOUT,
        )
        pids: list[int] = []

        for _ in range(3):
            async with client:
                result = await client.call_tool("pid")
                pid: int = result.data
                pids.append(pid)

            # Kill the subprocess
            psutil.Process(pid).kill()

            # Fail once to trigger cleanup
            with pytest.raises(Exception):
                async with client:
                    await client.call_tool("pid")

        # Each cycle should have started a new subprocess
        assert len(set(pids)) == 3

    async def test_crash_during_active_context(self, stdio_script):
        """When subprocess dies while the client context is open, recovery works on the next attempt."""
        client = Client(
            transport=PythonStdioTransport(script_path=stdio_script),
            init_timeout=self.INIT_TIMEOUT,
        )
        pid1: int = 0

        with pytest.raises(Exception):
            async with client:
                result = await client.call_tool("pid")
                pid1 = result.data
                # Kill while the context is still open
                psutil.Process(pid1).kill()
                # This call hits the dead session
                await client.call_tool("pid")

        assert pid1 != 0, "First call should have succeeded before the crash"

        # Recovery: next connection starts a fresh subprocess
        async with client:
            result = await client.call_tool("pid")
            pid2: int = result.data

        assert pid1 != pid2

    async def test_proxy_recovers_after_stdio_crash(self, stdio_script):
        """A proxy server wrapping a stdio backend recovers after the backend crashes."""
        from fastmcp.server import create_proxy

        backend_client = Client(
            transport=PythonStdioTransport(script_path=stdio_script),
            init_timeout=self.INIT_TIMEOUT,
        )
        proxy = create_proxy(target=backend_client, name="test-proxy")

        # First call works
        result1 = await proxy.call_tool("pid")
        pid1 = int(result1.content[0].text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

        # Kill the backend subprocess
        psutil.Process(pid1).kill()

        # First call after crash fails
        with pytest.raises(Exception):
            await proxy.call_tool("pid")

        # Second call recovers with a new subprocess
        result2 = await proxy.call_tool("pid")
        pid2 = int(result2.content[0].text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

        assert pid1 != pid2

    async def test_concurrent_requests_during_crash(self, stdio_script):
        """Multiple concurrent callers fail cleanly when subprocess dies, then recovery works."""
        from fastmcp.server import create_proxy

        backend_client = Client(
            transport=PythonStdioTransport(script_path=stdio_script),
            init_timeout=self.INIT_TIMEOUT,
        )
        proxy = create_proxy(target=backend_client, name="test-proxy")

        # First call to get the PID
        result = await proxy.call_tool("pid")
        pid1 = int(result.content[0].text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]

        # Kill the subprocess
        psutil.Process(pid1).kill()

        # Fire several concurrent requests. Depending on how quickly the dead
        # session is detected and a replacement spawned, each caller either
        # fails cleanly or lands on the fresh subprocess — with a fast-starting
        # server, recovery can beat all five requests and the crash is fully
        # transparent. What must never happen: a hang (gather returning is the
        # proof), or a "success" served by the killed process.
        tasks = [proxy.call_tool("pid") for _ in range(5)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if not isinstance(r, Exception):
                served_by = int(r.content[0].text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
                assert served_by != pid1, (
                    "call reported success from the killed subprocess"
                )

        # Recovery: a subsequent request must succeed on a fresh subprocess
        result = await proxy.call_tool("pid")
        pid2 = int(result.content[0].text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert pid1 != pid2

    async def test_clean_exit_recovers(self):
        """Recovery works when the subprocess exits cleanly (exit code 0), not just crashes."""
        client = Client(
            transport=PythonStdioTransport(
                script_path=MINIMAL_STDIO_SERVER,
                args=["--exit-after-calls", "2"],
            ),
            init_timeout=self.INIT_TIMEOUT,
        )

        async with client:
            result1 = await client.call_tool("pid")
            pid1: int = result1.data
            # Second call triggers delayed clean exit
            await client.call_tool("pid")

            # Wait for the subprocess to actually exit (it self-terminates
            # via a background timer ~0.1s after the second call) instead
            # of blindly sleeping past the worst case.
            await wait_for_process_exit(pid1)

        # Recovery after clean exit.
        #
        # The transport only notices a dead session once the SDK dispatcher's
        # read loop has observed EOF on the subprocess's stdout and set its
        # `_closed` flag (see `StdioTransport._is_session_dead`). The process
        # being gone does not imply that detection has happened yet: EOF has to
        # travel from the OS pipe through anyio's stream plumbing and then be
        # picked up by a separate read-loop task. On a loaded machine — notably
        # Windows CI running xdist workers on two cores — that can land after
        # `connect()` samples the flag, so the first attempt is routed to the
        # stale session and fails with CONNECTION_CLOSED, which in turn tears
        # the session down so the next attempt reconnects.
        #
        # The crash tests above encode this same one-failure-then-recover
        # contract explicitly with `pytest.raises`. Here the failure is
        # timing-dependent rather than guaranteed, so retry once instead: the
        # invariant under test is that a cleanly-exited server is replaced by a
        # fresh subprocess, not how many attempts EOF detection costs.
        pid2: int | None = None
        for _ in range(2):
            try:
                async with client:
                    result2 = await client.call_tool("pid")
                    pid2 = result2.data
                break
            except (MCPError, RuntimeError):
                continue

        assert pid2 is not None, "Client did not recover after a clean subprocess exit"
        assert pid1 != pid2

    async def test_crash_during_initialization(self, tmp_path):
        """Recovery works when subprocess crashes during the first connection attempt."""
        # Script that exits immediately — crashes before init completes
        crash_script = tmp_path / "crash_init.py"
        crash_script.write_text(
            inspect.cleandoc("""
            import sys
            sys.exit(1)
        """)
        )

        client = Client(
            transport=PythonStdioTransport(script_path=crash_script),
            init_timeout=self.INIT_TIMEOUT,
        )

        with pytest.raises(Exception):
            async with client:
                pass

        # Replace the same path with a working server. It delegates to the
        # minimal stdio server so the retry doesn't pay for a fastmcp import.
        crash_script.write_text(
            inspect.cleandoc(f"""
            import runpy

            runpy.run_path({str(MINIMAL_STDIO_SERVER)!r}, run_name="__main__")
        """)
        )

        # Recovery with the now-working script
        async with client:
            result = await client.call_tool("pid")
            assert isinstance(result.data, int)


@pytest.mark.subprocess_heavy
class TestLogFile:
    """Stderr capture, proven against a real FastMCP server.

    Unlike the rest of this module these spawn a full `import fastmcp`
    interpreter rather than the minimal stdlib server, because the point is
    that the log file captures a real server's stderr. That costs ~0.7s per
    spawn, so they run in the serial CI step.
    """

    @pytest.fixture
    def stdio_script_with_stderr(self, tmp_path):
        script = inspect.cleandoc('''
            import sys
            from fastmcp import FastMCP

            mcp = FastMCP()

            @mcp.tool
            def write_error(message: str) -> str:
                """Writes a message to stderr and returns it"""
                print(message, file=sys.stderr, flush=True)
                return message

            if __name__ == "__main__":
                mcp.run()
            ''')
        script_file = tmp_path / "stderr_script.py"
        script_file.write_text(script)
        return script_file

    async def test_log_file_parameter_accepted_by_stdio_transport(self, tmp_path):
        """Test that log_file parameter can be set on StdioTransport"""
        log_file_path = tmp_path / "errors.log"
        transport = StdioTransport(
            command="python", args=["script.py"], log_file=log_file_path
        )
        assert transport.log_file == log_file_path

    async def test_log_file_parameter_accepted_by_python_stdio_transport(
        self, tmp_path, stdio_script_with_stderr
    ):
        """Test that log_file parameter can be set on PythonStdioTransport"""
        log_file_path = tmp_path / "errors.log"
        transport = PythonStdioTransport(
            script_path=stdio_script_with_stderr, log_file=log_file_path
        )
        assert transport.log_file == log_file_path

    async def test_log_file_parameter_accepts_textio(self, tmp_path):
        """Test that log_file parameter can accept a TextIO object"""
        log_file_path = tmp_path / "errors.log"
        with open(log_file_path, "w") as log_file:
            transport = StdioTransport(
                command="python", args=["script.py"], log_file=log_file
            )
            assert transport.log_file == log_file

    async def test_log_file_captures_stderr_output_with_path(
        self, tmp_path, stdio_script_with_stderr
    ):
        """Test that stderr output is written to the log_file when using Path"""
        log_file_path = tmp_path / "errors.log"

        transport = PythonStdioTransport(
            script_path=stdio_script_with_stderr, log_file=log_file_path
        )
        client = Client(transport=transport)

        async with client:
            await client.call_tool("write_error", {"message": "Test error message"})

        content = await wait_for_log_content(log_file_path, "Test error message")
        assert "Test error message" in content

    async def test_log_file_captures_stderr_output_with_textio(
        self, tmp_path, stdio_script_with_stderr
    ):
        """Test that stderr output is written to the log_file when using TextIO"""
        log_file_path = tmp_path / "errors.log"

        with open(log_file_path, "w") as log_file:
            transport = PythonStdioTransport(
                script_path=stdio_script_with_stderr, log_file=log_file
            )
            client = Client(transport=transport)

            async with client:
                await client.call_tool(
                    "write_error", {"message": "Test error with TextIO"}
                )

            content = await wait_for_log_content(
                log_file_path, "Test error with TextIO"
            )

        assert "Test error with TextIO" in content

    async def test_log_file_none_uses_default_behavior(
        self, tmp_path, stdio_script_with_stderr
    ):
        """Test that log_file=None uses default stderr handling"""
        transport = PythonStdioTransport(
            script_path=stdio_script_with_stderr, log_file=None
        )
        client = Client(transport=transport)

        async with client:
            # Should work without error even without explicit log_file
            result = await client.call_tool(
                "write_error", {"message": "Default stderr"}
            )
            assert result.data == "Default stderr"
