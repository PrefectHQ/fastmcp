"""Test server argument passing functionality."""

from pathlib import Path

import pytest

from fastmcp.utilities.mcp_server_config import MCPServerConfig
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource


class TestServerArguments:
    """Test passing arguments to servers."""

    async def test_server_with_argparse(self, tmp_path):
        """Test a server that uses argparse with command line arguments."""
        server_file = tmp_path / "argparse_server.py"
        server_file.write_text("""
import argparse
from fastmcp import FastMCP

parser = argparse.ArgumentParser()
parser.add_argument("--name", default="DefaultServer")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--debug", action="store_true")

args = parser.parse_args()

server_name = f"{args.name}:{args.port}"
if args.debug:
    server_name += " (Debug)"

mcp = FastMCP(server_name)

@mcp.tool
def get_config() -> dict:
    return {"name": args.name, "port": args.port, "debug": args.debug}
""")

        # Test with arguments
        source = FileSystemSource(path=str(server_file))
        config = MCPServerConfig(source=source)

        from fastmcp.cli.cli import with_argv

        # Simulate passing arguments
        with with_argv(["--name", "TestServer", "--port", "9000", "--debug"]):
            server = await config.source.load_server()

        assert server.name == "TestServer:9000 (Debug)"

        # Test the tool works and can access the parsed args
        tools = await server.list_tools()
        assert any(t.name == "get_config" for t in tools)

    async def test_server_with_no_args(self, tmp_path):
        """Test a server that uses argparse with no arguments (defaults)."""
        server_file = tmp_path / "default_server.py"
        server_file.write_text("""
import argparse
from fastmcp import FastMCP

parser = argparse.ArgumentParser()
parser.add_argument("--name", default="DefaultName")
args = parser.parse_args()

mcp = FastMCP(args.name)
""")

        source = FileSystemSource(path=str(server_file))
        config = MCPServerConfig(source=source)

        from fastmcp.cli.cli import with_argv

        # Test with empty args list (should use defaults)
        with with_argv([]):
            server = await config.source.load_server()

        assert server.name == "DefaultName"

    async def test_server_with_sys_argv_access(self, tmp_path):
        """Test a server that directly accesses sys.argv."""
        server_file = tmp_path / "sysargv_server.py"
        server_file.write_text("""
import sys
from fastmcp import FastMCP

# Direct sys.argv access (less common but should work)
name = "DirectServer"
if len(sys.argv) > 1 and sys.argv[1] == "--custom":
    name = "CustomServer"

mcp = FastMCP(name)
""")

        source = FileSystemSource(path=str(server_file))
        config = MCPServerConfig(source=source)

        from fastmcp.cli.cli import with_argv

        # Test with custom argument
        with with_argv(["--custom"]):
            server = await config.source.load_server()

        assert server.name == "CustomServer"

        # Test without argument
        with with_argv([]):
            server = await config.source.load_server()

        assert server.name == "DirectServer"

    async def test_config_server_example(self):
        """Test the actual config_server.py example."""
        # Find the examples directory
        examples_dir = Path(__file__).parent.parent.parent / "examples"
        config_server = examples_dir / "config_server.py"

        if not config_server.exists():
            pytest.skip("config_server.py example not found")

        source = FileSystemSource(path=str(config_server))
        config = MCPServerConfig(source=source)

        from fastmcp.cli.cli import with_argv

        # Test with debug flag
        with with_argv(["--name", "TestExample", "--debug"]):
            server = await config.source.load_server()

        assert server.name == "TestExample (Debug)"

        # Verify tools are available
        tools = await server.list_tools()
        assert any(t.name == "get_status" for t in tools)
        assert any(t.name == "echo_message" for t in tools)
