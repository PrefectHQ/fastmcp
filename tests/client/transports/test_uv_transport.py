import inspect
import sys
import tempfile
from pathlib import Path

import pytest

import fastmcp
from fastmcp.client import Client
from fastmcp.client.client import CallToolResult
from fastmcp.client.transports import StdioTransport, UvStdioTransport

# Detect if running from dev install to use local source instead of PyPI
_is_dev_install = "dev" in fastmcp.__version__
_fastmcp_src_dir = (
    Path(__file__).parent.parent.parent.parent if _is_dev_install else None
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

        # In dev installs, use --with-editable to install local source.
        # In releases, use --with to install from PyPI.
        if _is_dev_install and _fastmcp_src_dir:
            transport: StdioTransport = StdioTransport(
                command="uv",
                args=[
                    "run",
                    "--directory",
                    tmpdir,
                    "--with-editable",
                    str(_fastmcp_src_dir),
                    "--module",
                    "my_module",
                ],
                keep_alive=False,
            )
        else:
            transport = UvStdioTransport(
                with_packages=["fastmcp"],
                command="my_module",
                module=True,
                project_directory=Path(tmpdir),
                keep_alive=False,
            )

        client: Client[StdioTransport] = Client(transport=transport)

        async with client:
            result: CallToolResult = await client.call_tool("add", {"x": 1, "y": 2})
            sum: int = result.data  # pyright: ignore[reportAny]

        # Explicitly close the transport to ensure subprocess cleanup
        await client.transport.close()
        assert sum == 3
