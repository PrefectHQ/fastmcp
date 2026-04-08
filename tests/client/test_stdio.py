import asyncio
import gc
import inspect
import os
import time
import weakref

import psutil
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.transports import PythonStdioTransport, StdioTransport


def running_under_debugger():
    return os.environ.get("DEBUGPY_RUNNING") == "true"


def gc_collect_harder():
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()
    gc.collect()


class TestParallelCalls:
    @pytest.fixture
    def stdio_script(self, tmp_path):
        script = inspect.cleandoc('''
            import os
            from fastmcp import FastMCP

            mcp = FastMCP()

            @mcp.tool
            def pid() -> int:
                """Gets PID of server"""
                return os.getpid()

            if __name__ == "__main__":
                mcp.run()
            ''')
        script_file = tmp_path / "stdio.py"
        script_file.write_text(script)
        return script_file

    async def test_parallel_calls(self, stdio_script):
        backend_transport = PythonStdioTransport(script_path=stdio_script)
        backend_client = Client(transport=backend_transport)

        proxy = FastMCP.as_proxy(backend=backend_client, name="PROXY")

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
    def stdio_script(self, tmp_path):
        script = inspect.cleandoc('''
            import os
            from fastmcp import FastMCP

            mcp = FastMCP()

            @mcp.tool
            def pid() -> int:
                """Gets PID of server"""
                return os.getpid()

            if __name__ == "__main__":
                mcp.run()
            ''')
        script_file = tmp_path / "stdio.py"
        script_file.write_text(script)
        return script_file

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

        # Poll for process exit with a bounded timeout
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                psutil.Process(pid)
            except psutil.NoSuchProcess:
                return  # Process exited as expected
            await asyncio.sleep(0.1)
        pytest.fail(f"Process {pid} still alive after 10s")

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

        # Poll for process exit with a bounded timeout
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                psutil.Process(pid)
            except psutil.NoSuchProcess:
                return  # Process exited as expected
            await asyncio.sleep(0.1)
        pytest.fail(f"Process {pid} still alive after 10s")

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
    def stdio_script(self, tmp_path):
        script = inspect.cleandoc('''
            import os
            from fastmcp import FastMCP

            mcp = FastMCP()

            @mcp.tool
            def pid() -> int:
                """Gets PID of server"""
                return os.getpid()

            if __name__ == "__main__":
                mcp.run()
            ''')
        script_file = tmp_path / "stdio.py"
        script_file.write_text(script)
        return script_file

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

        # Fire several concurrent requests — all should fail, none should hang
        tasks = [proxy.call_tool("pid") for _ in range(5)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) > 0

        # Recovery: a subsequent request should succeed
        result = await proxy.call_tool("pid")
        pid2 = int(result.content[0].text)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
        assert pid1 != pid2

    async def test_clean_exit_recovers(self, tmp_path):
        """Recovery works when the subprocess exits cleanly (exit code 0), not just crashes."""
        script = tmp_path / "exit_script.py"
        script.write_text(
            inspect.cleandoc('''
            import os, sys, threading
            from fastmcp import FastMCP

            mcp = FastMCP()
            call_count = 0

            @mcp.tool
            def pid_then_exit() -> int:
                """Returns PID, exits cleanly after second call."""
                global call_count
                call_count += 1
                pid = os.getpid()
                if call_count >= 2:
                    threading.Timer(0.1, lambda: os._exit(0)).start()
                return pid

            if __name__ == "__main__":
                mcp.run()
        ''')
        )

        client = Client(
            transport=PythonStdioTransport(script_path=script),
            init_timeout=self.INIT_TIMEOUT,
        )

        async with client:
            result1 = await client.call_tool("pid_then_exit")
            pid1: int = result1.data
            # Second call triggers delayed clean exit
            await client.call_tool("pid_then_exit")
            await asyncio.sleep(0.3)

        # Recovery after clean exit
        async with client:
            result2 = await client.call_tool("pid_then_exit")
            pid2: int = result2.data

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

        # Write a working script to the same path
        crash_script.write_text(
            inspect.cleandoc("""
            import os
            from fastmcp import FastMCP

            mcp = FastMCP()

            @mcp.tool
            def pid() -> int:
                return os.getpid()

            if __name__ == "__main__":
                mcp.run()
        """)
        )

        # Recovery with the now-working script
        async with client:
            result = await client.call_tool("pid")
            assert isinstance(result.data, int)


class TestLogFile:
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

        # Need to wait a bit for stderr to flush
        await asyncio.sleep(0.1)

        content = log_file_path.read_text()
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

            # Need to wait a bit for stderr to flush
            await asyncio.sleep(0.1)

        content = log_file_path.read_text()
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
