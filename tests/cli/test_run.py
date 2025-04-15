from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from fastmcp import FastMCP
from fastmcp.cli.cli import app


@pytest.fixture
def server_file(tmp_path):
    """Create a simple server file for testing"""
    server_path = tmp_path / "test_server.py"
    server_path.write_text(
        """
from fastmcp import FastMCP

mcp = FastMCP(name="TestServer")

@mcp.tool()
def hello(name: str) -> str:
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()
"""
    )
    return server_path


def test_cli_run_transport_kwargs():
    """Test that transport_kwargs are correctly passed from CLI to server.run()"""
    runner = CliRunner()

    # Need to mock both the file parsing and the server import
    with (
        patch("fastmcp.cli.cli._parse_file_path") as mock_parse_file_path,
        patch("fastmcp.cli.cli._import_server") as mock_import_server,
    ):
        # Make _parse_file_path return a fake path and server object
        mock_parse_file_path.return_value = (Path("fake_server.py"), "mcp")

        # Create a mock server with a mock run method
        mock_server = FastMCP(name="MockServer")
        mock_server.run = Mock()

        # Make _import_server return our mock server
        mock_import_server.return_value = mock_server

        # Run the CLI command with transport_kwargs
        result = runner.invoke(
            app,
            [
                "run",
                "fake_server.py",
                "--transport",
                "sse",
                "--host",
                "127.0.0.1",
                "--port",
                "9000",
                "--log-level",
                "DEBUG",
            ],
        )

        # Check that the run method was called with the correct kwargs
        mock_server.run.assert_called_once_with(
            transport="sse",
            host="127.0.0.1",
            port=9000,
            log_level="DEBUG",
        )

        # Check CLI command succeeded
        assert result.exit_code == 0
