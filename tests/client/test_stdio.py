import asyncio
import gc
import inspect
import os
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


class TestKeepAlive:
    # https://github.com/jlowin/fastmcp/issues/581

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

        with pytest.raises(psutil.NoSuchProcess):
            while True:
                psutil.Process(pid)
                await asyncio.sleep(0.1)

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

        with pytest.raises(psutil.NoSuchProcess):
            while True:
                psutil.Process(pid)
                await asyncio.sleep(0.1)

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
