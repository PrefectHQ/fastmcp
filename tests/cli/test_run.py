import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fastmcp.cli.run import (
    create_mcp_config_server,
    is_url,
)
from fastmcp.client.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.mcp_config import MCPConfig, StdioMCPServer
from fastmcp.server.server import FastMCP
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource


class TestUrlDetection:
    """Test URL detection functionality."""

    def test_is_url_valid_http(self):
        """Test detection of valid HTTP URLs."""
        assert is_url("http://example.com")
        assert is_url("http://localhost:8080")
        assert is_url("http://127.0.0.1:3000/path")

    def test_is_url_valid_https(self):
        """Test detection of valid HTTPS URLs."""
        assert is_url("https://example.com")
        assert is_url("https://api.example.com/mcp")
        assert is_url("https://localhost:8443")

    def test_is_url_invalid(self):
        """Test detection of non-URLs."""
        assert not is_url("server.py")
        assert not is_url("/path/to/server.py")
        assert not is_url("server.py:app")
        assert not is_url("ftp://example.com")  # Not http/https
        assert not is_url("file:///path/to/file")


class TestFileSystemSource:
    """Test FileSystemSource path parsing functionality."""

    def test_parse_simple_path(self, tmp_path):
        """Test parsing simple file path without object."""
        test_file = tmp_path / "server.py"
        test_file.write_text("# test server")

        source = FileSystemSource(path=str(test_file))
        assert Path(source.path).resolve() == test_file.resolve()
        assert source.entrypoint is None

    def test_parse_path_with_object(self, tmp_path):
        """Test parsing file path with object specification."""
        test_file = tmp_path / "server.py"
        test_file.write_text("# test server")

        source = FileSystemSource(path=f"{test_file}:app")
        assert Path(source.path).resolve() == test_file.resolve()
        assert source.entrypoint == "app"

    def test_parse_complex_object(self, tmp_path):
        """Test parsing file path with complex object specification."""
        test_file = tmp_path / "server.py"
        test_file.write_text("# test server")

        # The implementation splits on the last colon, so file:module:app
        # becomes file_path="file:module" and entrypoint="app"
        # We need to create a file with a colon in the name for this test
        complex_file = tmp_path / "server:module.py"
        complex_file.write_text("# test server")

        source = FileSystemSource(path=f"{complex_file}:app")
        assert Path(source.path).resolve() == complex_file.resolve()
        assert source.entrypoint == "app"

    async def test_load_server_nonexistent(self):
        """Test loading nonexistent file path exits."""
        source = FileSystemSource(path="nonexistent.py")
        with pytest.raises(SystemExit) as exc_info:
            await source.load_server()
        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1

    async def test_load_server_directory(self, tmp_path):
        """Test loading directory path exits."""
        source = FileSystemSource(path=str(tmp_path))
        with pytest.raises(SystemExit) as exc_info:
            await source.load_server()
        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1


class TestMCPConfig:
    """Test MCPConfig functionality."""

    async def test_run_mcp_config(self, tmp_path: Path):
        """Test creating a server from an MCPConfig file."""
        server_script = inspect.cleandoc("""
            from fastmcp import FastMCP

            mcp = FastMCP()

            @mcp.tool
            def add(a: int, b: int) -> int:
                return a + b

            if __name__ == '__main__':
                mcp.run()
            """)

        script_path: Path = tmp_path / "test.py"
        script_path.write_text(server_script)

        mcp_config_path = tmp_path / "mcp_config.json"

        mcp_config = MCPConfig(
            mcpServers={
                "test_server": StdioMCPServer(command="python", args=[str(script_path)])
            }
        )
        mcp_config.write_to_file(mcp_config_path)

        server: FastMCP[None] = create_mcp_config_server(mcp_config_path)

        client = Client[FastMCPTransport](server)

        async with client:
            tools = await client.list_tools()
            assert len(tools) == 1

    async def test_validate_mcp_config(self, tmp_path: Path):
        """Test creating a server from an MCPConfig file."""

        mcp_config_path = tmp_path / "mcp_config.json"

        mcp_config = {"mcpServers": {"test_server": dict(x=1, y=2)}}
        with mcp_config_path.open("w") as f:
            json.dump(mcp_config, f)

        with pytest.raises(ValidationError, match="validation errors for MCPConfig"):
            create_mcp_config_server(mcp_config_path)


class TestServerImport:
    """Test server import functionality using real files."""

    async def test_import_server_basic_mcp(self, tmp_path):
        """Test importing server with basic FastMCP server."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

mcp = fastmcp.FastMCP("TestServer")

@mcp.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"
""")

        source = FileSystemSource(path=str(test_file))
        server = await source.load_server()
        assert server.name == "TestServer"
        tools = await server.list_tools()
        assert any(t.name == "greet" for t in tools)

    async def test_import_server_with_main_block(self, tmp_path):
        """Test importing server with if __name__ == '__main__' block."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

app = fastmcp.FastMCP("MainServer")

@app.tool
def calculate(x: int, y: int) -> int:
    return x + y

if __name__ == "__main__":
    app.run()
""")

        source = FileSystemSource(path=str(test_file))
        server = await source.load_server()
        assert server.name == "MainServer"
        tools = await server.list_tools()
        assert any(t.name == "calculate" for t in tools)

    async def test_import_server_standard_names(self, tmp_path):
        """Test automatic detection of standard names (mcp, server, app)."""
        # Test with 'mcp' name
        mcp_file = tmp_path / "mcp_server.py"
        mcp_file.write_text("""
import fastmcp
mcp = fastmcp.FastMCP("MCPServer")
""")

        source = FileSystemSource(path=str(mcp_file))
        server = await source.load_server()
        assert server.name == "MCPServer"

        # Test with 'server' name
        server_file = tmp_path / "server_server.py"
        server_file.write_text("""
import fastmcp
server = fastmcp.FastMCP("ServerServer")
""")

        source = FileSystemSource(path=str(server_file))
        server = await source.load_server()
        assert server.name == "ServerServer"

        # Test with 'app' name
        app_file = tmp_path / "app_server.py"
        app_file.write_text("""
import fastmcp
app = fastmcp.FastMCP("AppServer")
""")

        source = FileSystemSource(path=str(app_file))
        server = await source.load_server()
        assert server.name == "AppServer"

    async def test_import_server_nonstandard_name(self, tmp_path):
        """Test importing server with non-standard object name."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

my_custom_server = fastmcp.FastMCP("CustomServer")

@my_custom_server.tool
def custom_tool() -> str:
    return "custom"
""")

        source = FileSystemSource(path=f"{test_file}:my_custom_server")
        server = await source.load_server()
        assert server.name == "CustomServer"
        tools = await server.list_tools()
        assert any(t.name == "custom_tool" for t in tools)

    async def test_import_server_no_standard_names_fails(self, tmp_path):
        """Test importing server when no standard names exist fails."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

other_name = fastmcp.FastMCP("OtherServer")
""")

        source = FileSystemSource(path=str(test_file))
        with pytest.raises(SystemExit) as exc_info:
            await source.load_server()
        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1

    async def test_import_server_nonexistent_object_fails(self, tmp_path):
        """Test importing nonexistent server object fails."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

mcp = fastmcp.FastMCP("TestServer")
""")

        source = FileSystemSource(path=f"{test_file}:nonexistent")
        with pytest.raises(SystemExit) as exc_info:
            await source.load_server()

        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1


class TestV1ServerAsync:
    """Test FastMCP 1.x server async support."""

    async def test_run_v1_server_stdio(self, tmp_path):
        """Test that v1 server uses async stdio method."""
        from unittest.mock import AsyncMock, patch

        from mcp.server.fastmcp import FastMCP as FastMCP1x

        from fastmcp.cli.run import run_command

        # Create a v1 FastMCP server file with both sync and async tools
        test_file = tmp_path / "v1_server.py"
        test_file.write_text("""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("V1Server")

@mcp.tool()
def sync_echo(text: str) -> str:
    '''Sync tool for testing'''
    return f"sync: {text}"

@mcp.tool()
async def async_echo(text: str) -> str:
    '''Async tool for testing'''
    return f"async: {text}"
""")

        # Mock the async run method
        with patch.object(
            FastMCP1x, "run_stdio_async", new_callable=AsyncMock
        ) as run_mock:
            await run_command(str(test_file), transport="stdio")
            run_mock.assert_called_once()

    async def test_run_v1_server_http(self, tmp_path):
        """Test that v1 server uses async http method."""
        from unittest.mock import AsyncMock, patch

        from mcp.server.fastmcp import FastMCP as FastMCP1x

        from fastmcp.cli.run import run_command

        # Create a v1 FastMCP server file with both sync and async tools
        test_file = tmp_path / "v1_server.py"
        test_file.write_text("""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("V1Server")

@mcp.tool()
def sync_echo(text: str) -> str:
    '''Sync tool for testing'''
    return f"sync: {text}"

@mcp.tool()
async def async_echo(text: str) -> str:
    '''Async tool for testing'''
    return f"async: {text}"
""")

        # Mock the async run method
        with patch.object(
            FastMCP1x, "run_streamable_http_async", new_callable=AsyncMock
        ) as run_mock:
            await run_command(str(test_file), transport="http")
            run_mock.assert_called_once()

    async def test_run_v1_server_streamable_http(self, tmp_path):
        """Test that v1 server uses async streamable-http method."""
        from unittest.mock import AsyncMock, patch

        from mcp.server.fastmcp import FastMCP as FastMCP1x

        from fastmcp.cli.run import run_command

        # Create a v1 FastMCP server file with both sync and async tools
        test_file = tmp_path / "v1_server.py"
        test_file.write_text("""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("V1Server")

@mcp.tool()
def sync_echo(text: str) -> str:
    '''Sync tool for testing'''
    return f"sync: {text}"

@mcp.tool()
async def async_echo(text: str) -> str:
    '''Async tool for testing'''
    return f"async: {text}"
""")

        # Mock the async run method
        with patch.object(
            FastMCP1x, "run_streamable_http_async", new_callable=AsyncMock
        ) as run_mock:
            await run_command(str(test_file), transport="streamable-http")
            run_mock.assert_called_once()

    async def test_run_v1_server_sse(self, tmp_path):
        """Test that v1 server uses async sse method."""
        from unittest.mock import AsyncMock, patch

        from mcp.server.fastmcp import FastMCP as FastMCP1x

        from fastmcp.cli.run import run_command

        # Create a v1 FastMCP server file with both sync and async tools
        test_file = tmp_path / "v1_server.py"
        test_file.write_text("""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("V1Server")

@mcp.tool()
def sync_echo(text: str) -> str:
    '''Sync tool for testing'''
    return f"sync: {text}"

@mcp.tool()
async def async_echo(text: str) -> str:
    '''Async tool for testing'''
    return f"async: {text}"
""")

        # Mock the async run method
        with patch.object(
            FastMCP1x, "run_sse_async", new_callable=AsyncMock
        ) as run_mock:
            await run_command(str(test_file), transport="sse")
            run_mock.assert_called_once()

    async def test_run_v1_server_default_transport(self, tmp_path):
        """Test that v1 server uses streamable-http by default."""
        from unittest.mock import AsyncMock, patch

        from mcp.server.fastmcp import FastMCP as FastMCP1x

        from fastmcp.cli.run import run_command

        # Create a v1 FastMCP server file with both sync and async tools
        test_file = tmp_path / "v1_server.py"
        test_file.write_text("""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("V1Server")

@mcp.tool()
def sync_echo(text: str) -> str:
    '''Sync tool for testing'''
    return f"sync: {text}"

@mcp.tool()
async def async_echo(text: str) -> str:
    '''Async tool for testing'''
    return f"async: {text}"
""")

        # Mock the async run method
        with patch.object(
            FastMCP1x, "run_streamable_http_async", new_callable=AsyncMock
        ) as run_mock:
            await run_command(str(test_file))
            run_mock.assert_called_once()

    async def test_run_v1_server_with_host_port(self, tmp_path):
        """Test that v1 server receives host/port settings."""
        from unittest.mock import AsyncMock, patch

        from mcp.server.fastmcp import FastMCP as FastMCP1x

        from fastmcp.cli.run import run_command

        # Create a v1 FastMCP server file with both sync and async tools
        test_file = tmp_path / "v1_server.py"
        test_file.write_text("""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("V1Server")

@mcp.tool()
def sync_echo(text: str) -> str:
    '''Sync tool for testing'''
    return f"sync: {text}"

@mcp.tool()
async def async_echo(text: str) -> str:
    '''Async tool for testing'''
    return f"async: {text}"
""")

        # Mock the async run method
        with patch.object(
            FastMCP1x, "run_streamable_http_async", new_callable=AsyncMock
        ) as run_mock:
            await run_command(
                str(test_file), transport="http", host="0.0.0.0", port=9000
            )
            run_mock.assert_called_once()


class TestSkipSource:
    """Test the --skip-source functionality."""

    async def test_run_command_calls_prepare_by_default(self, tmp_path):
        """Test that run_command calls source.prepare() by default."""
        from unittest.mock import AsyncMock, patch

        from fastmcp.cli.run import run_command

        # Create a test server file
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp
mcp = fastmcp.FastMCP("TestServer")
""")

        # Create a test config file
        config_file = tmp_path / "fastmcp.json"
        config_data = {"source": {"path": str(test_file), "entrypoint": "mcp"}}
        config_file.write_text(json.dumps(config_data))

        # Mock the prepare method and server run
        with (
            patch.object(
                FileSystemSource, "prepare", new_callable=AsyncMock
            ) as prepare_mock,
            patch("fastmcp.server.server.FastMCP.run_async", new_callable=AsyncMock),
        ):
            # Run the command
            await run_command(str(config_file))

            # Verify prepare was called
            prepare_mock.assert_called_once()

    async def test_run_command_skips_prepare_with_flag(self, tmp_path):
        """Test that run_command skips source.prepare() when skip_source=True."""
        from unittest.mock import AsyncMock, patch

        from fastmcp.cli.run import run_command

        # Create a test server file
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp
mcp = fastmcp.FastMCP("TestServer")
""")

        # Create a test config file
        config_file = tmp_path / "fastmcp.json"
        config_data = {"source": {"path": str(test_file), "entrypoint": "mcp"}}
        config_file.write_text(json.dumps(config_data))

        # Mock the prepare method and server run
        with (
            patch.object(
                FileSystemSource, "prepare", new_callable=AsyncMock
            ) as prepare_mock,
            patch("fastmcp.server.server.FastMCP.run_async", new_callable=AsyncMock),
        ):
            # Run the command with skip_source=True
            await run_command(str(config_file), skip_source=True)

            # Verify prepare was NOT called
            prepare_mock.assert_not_called()

    async def test_filesystem_source_prepare_by_default(self, tmp_path):
        """Test that FileSystemSource is prepared when using direct file spec."""
        from unittest.mock import AsyncMock, patch

        from fastmcp.cli.run import run_command
        from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import (
            FileSystemSource,
        )

        # Create a test server file
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp
mcp = fastmcp.FastMCP("TestServer")
""")

        # Mock the prepare method and server run
        with (
            patch.object(
                FileSystemSource, "prepare", new_callable=AsyncMock
            ) as prepare_mock,
            patch("fastmcp.server.server.FastMCP.run_async", new_callable=AsyncMock),
        ):
            # Run with direct file specification
            await run_command(str(test_file))

            # Verify prepare was called
            prepare_mock.assert_called_once()

    async def test_filesystem_source_skip_prepare_with_flag(self, tmp_path):
        """Test that FileSystemSource.prepare() is skipped with skip_source flag."""
        from unittest.mock import AsyncMock, patch

        from fastmcp.cli.run import run_command
        from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import (
            FileSystemSource,
        )

        # Create a test server file
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp
mcp = fastmcp.FastMCP("TestServer")
""")

        # Mock the prepare method and server run
        with (
            patch.object(
                FileSystemSource, "prepare", new_callable=AsyncMock
            ) as prepare_mock,
            patch("fastmcp.server.server.FastMCP.run_async", new_callable=AsyncMock),
        ):
            # Run with direct file specification and skip_source=True
            await run_command(str(test_file), skip_source=True)

            # Verify prepare was NOT called
            prepare_mock.assert_not_called()


class TestReloadFunctionality:
    """Test reload functionality."""

    def test_python_file_filter_accepts_py_files(self):
        """Test that Python file filter accepts .py files."""
        from watchfiles import Change

        from fastmcp.cli.run import _python_file_filter

        assert _python_file_filter(Change.modified, "/path/to/file.py") is True
        assert _python_file_filter(Change.added, "server.py") is True
        assert _python_file_filter(Change.deleted, "/some/dir/module.py") is True

    def test_python_file_filter_rejects_non_py_files(self):
        """Test that Python file filter rejects non-.py files."""
        from watchfiles import Change

        from fastmcp.cli.run import _python_file_filter

        assert _python_file_filter(Change.modified, "/path/to/file.txt") is False
        assert _python_file_filter(Change.modified, "/path/to/file.js") is False
        assert _python_file_filter(Change.modified, "/path/to/file.pyc") is False
        assert _python_file_filter(Change.modified, "/path/to/.py") is True  # Edge case
        assert _python_file_filter(Change.modified, "Dockerfile") is False
