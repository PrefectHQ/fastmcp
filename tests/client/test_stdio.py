import inspect

import pytest

from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport, StdioTransport


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
            pid1 = int(result1[0].text)  # type: ignore[attr-defined]

        async with client:
            result2 = await client.call_tool("pid")
            pid2 = int(result2[0].text)  # type: ignore[attr-defined]

        assert pid1 == pid2

    async def test_keep_alive_false_starts_new_session_across_multiple_calls(
        self, stdio_script
    ):
        client = Client(
            transport=PythonStdioTransport(script_path=stdio_script, keep_alive=False)
        )
        assert client.transport.keep_alive is False

        async with client:
            result1 = await client.call_tool("pid")
            pid1 = int(result1[0].text)  # type: ignore[attr-defined]

        async with client:
            result2 = await client.call_tool("pid")
            pid2 = int(result2[0].text)  # type: ignore[attr-defined]

        assert pid1 != pid2

    async def test_keep_alive_starts_new_session_if_manually_closed(self, stdio_script):
        client = Client(transport=PythonStdioTransport(script_path=stdio_script))
        assert client.transport.keep_alive is True

        async with client:
            result1 = await client.call_tool("pid")
            pid1 = int(result1[0].text)  # type: ignore[attr-defined]

        await client.close()

        async with client:
            result2 = await client.call_tool("pid")
            pid2 = int(result2[0].text)  # type: ignore[attr-defined]

        assert pid1 != pid2

    async def test_keep_alive_maintains_session_if_reentered(self, stdio_script):
        client = Client(transport=PythonStdioTransport(script_path=stdio_script))
        assert client.transport.keep_alive is True

        async with client:
            result1 = await client.call_tool("pid")
            pid1 = int(result1[0].text)  # type: ignore[attr-defined]

            async with client:
                result2 = await client.call_tool("pid")
                pid2 = int(result2[0].text)  # type: ignore[attr-defined]

            result3 = await client.call_tool("pid")
            pid3 = int(result3[0].text)  # type: ignore[attr-defined]

        assert pid1 == pid2 == pid3

    async def test_close_session_and_try_to_use_client_raises_error(self, stdio_script):
        client = Client(transport=PythonStdioTransport(script_path=stdio_script))
        assert client.transport.keep_alive is True

        async with client:
            await client.close()
            with pytest.raises(RuntimeError, match="Client is not connected"):
                await client.call_tool("pid")
