import inspect
import sys
import tempfile
from pathlib import Path

import pytest

from fastmcp.client import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import (
    UvStdioTransport,
)


@pytest.mark.timeout(10)
@pytest.mark.client_process
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows file locking issues with uv client process cleanup",
)
async def test_uv_transport():
    with tempfile.TemporaryDirectory() as tmpdir:
        script: str = inspect.cleandoc('''
            from fastmcp import FastMCP

            mcp = FastMCP()

            @mcp.tool
            def add(x: int, y: int) -> int:
                """Adds two numbers together"""
                return x + y

            if __name__ == "__main__":
                mcp.run()
            ''')
        script_file: Path = Path(tmpdir) / "uv.py"
        _ = script_file.write_text(script)

        client: Client[UvStdioTransport] = Client(
            transport=UvStdioTransport(command=str(script_file), keep_alive=False)
        )

        async with client:
            result: CallToolResult = await client.call_tool("add", {"x": 1, "y": 2})
            sum: int = result.data  # pyright: ignore[reportAny]

        # Explicitly close the transport to ensure subprocess cleanup
        await client.transport.close()
        assert sum == 3


@pytest.mark.timeout(10)
@pytest.mark.client_process
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows file locking issues with uv client process cleanup",
)
async def test_uv_transport_module():
    with tempfile.TemporaryDirectory() as tmpdir:
        module_dir = Path(tmpdir) / "my_module"
        module_dir.mkdir()
        module_script = inspect.cleandoc('''
            from fastmcp import FastMCP

            mcp = FastMCP()

            @mcp.tool
            def add(x: int, y: int) -> int:
                """Adds two numbers together"""
                return x + y
            ''')
        script_file: Path = module_dir / "module.py"
        _ = script_file.write_text(module_script)

        main_script: str = inspect.cleandoc("""
            from .module import mcp
            mcp.run()
        """)
        main_file = module_dir / "__main__.py"
        _ = main_file.write_text(main_script)

        client: Client[UvStdioTransport] = Client(
            transport=UvStdioTransport(
                with_packages=["fastmcp"],
                command="my_module",
                module=True,
                project_directory=tmpdir,
                keep_alive=False,
            )
        )

        async with client:
            result: CallToolResult = await client.call_tool("add", {"x": 1, "y": 2})
            sum: int = result.data  # pyright: ignore[reportAny]

        # Explicitly close the transport to ensure subprocess cleanup
        await client.transport.close()
        assert sum == 3
