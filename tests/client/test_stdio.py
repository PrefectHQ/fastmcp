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

        tasks = [proxy.get_tools() for _ in range(count)]

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
