"""Tests for the CLI module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import fastmcp.cli.run
from fastmcp.cli import cli

# Set up test runner
runner = CliRunner()


@pytest.fixture
def mock_console():
    """Mock the rich console to test output."""
    with patch("fastmcp.cli.cli.console") as mock_console:
        yield mock_console


@pytest.fixture
def mock_logger():
    """Mock the logger to test logging."""
    with patch("fastmcp.cli.cli.logger") as mock_logger:
        yield mock_logger


@pytest.fixture
def mock_exit():
    """Mock sys.exit to prevent tests from exiting."""
    with patch("sys.exit") as mock_exit:
        yield mock_exit


@pytest.fixture
def temp_python_file(tmp_path):
    """Create a temporary Python file with a test server."""
    server_code = """
from mcp import Server

class TestServer(Server):
    name = "test_server"
    dependencies = ["package1", "package2"]

    def run(self, **kwargs):
        print("Running server with", kwargs)

mcp = TestServer()
server = TestServer()
app = TestServer()
custom_server = TestServer()
"""
    file_path = tmp_path / "test_server.py"
    file_path.write_text(server_code)
    return file_path


@pytest.fixture
def temp_env_file(tmp_path):
    """Create a temporary .env file."""
    env_content = """
TEST_VAR1=value1
TEST_VAR2=value2
"""
    env_path = tmp_path / ".env"
    env_path.write_text(env_content)
    return env_path


class TestHelperFunctions:
    def test_parse_file_path_simple(self):
        """Test parsing simple file path."""
        with (
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.is_file") as mock_is_file,
            patch("pathlib.Path.expanduser") as mock_expanduser,
            patch("pathlib.Path.resolve") as mock_resolve,
        ):
            mock_exists.return_value = True
            mock_is_file.return_value = True
            mock_expanduser.return_value = Path("file.py")
            mock_resolve.return_value = Path("file.py")

            path, obj = fastmcp.cli.run.parse_file_path("file.py")
            assert path == Path("file.py")
            assert obj is None

    def test_parse_file_path_with_object(self):
        """Test parsing file path with object."""
        with (
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.is_file") as mock_is_file,
            patch("pathlib.Path.expanduser") as mock_expanduser,
            patch("pathlib.Path.resolve") as mock_resolve,
        ):
            mock_exists.return_value = True
            mock_is_file.return_value = True
            mock_expanduser.return_value = Path("file.py")
            mock_resolve.return_value = Path("file.py")

            path, obj = fastmcp.cli.run.parse_file_path("file.py:server")
            assert path == Path("file.py")
            assert obj == "server"

    def test_parse_file_path_windows(self):
        """Test parsing Windows file path."""
        with (
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.is_file") as mock_is_file,
            patch("pathlib.Path.expanduser") as mock_expanduser,
            patch("pathlib.Path.resolve") as mock_resolve,
        ):
            mock_exists.return_value = True
            mock_is_file.return_value = True
            mock_expanduser.return_value = Path("C:/path/file.py")
            mock_resolve.return_value = Path("C:/path/file.py")

            path, obj = fastmcp.cli.run.parse_file_path("C:/path/file.py:server")
            assert path == Path("C:/path/file.py")
            assert obj == "server"

    def test_parse_file_path_not_file(self, mock_exit):
        """Test parsing path that is not a file."""
        with (
            patch("pathlib.Path.exists") as mock_exists,
            patch("pathlib.Path.is_file") as mock_is_file,
            patch("pathlib.Path.expanduser") as mock_expanduser,
            patch("pathlib.Path.resolve") as mock_resolve,
            patch("fastmcp.cli.run.logger") as mock_logger,
        ):
            mock_exists.return_value = True
            mock_is_file.return_value = False
            mock_expanduser.return_value = Path("directory")
            mock_resolve.return_value = Path("directory")

            fastmcp.cli.run.parse_file_path("directory")
            mock_logger.error.assert_called_once()
            mock_exit.assert_called_once_with(1)


class TestRunCommand:
    """Tests for the run command."""

    def test_run_command_success(self, temp_python_file):
        """Test successful run command execution."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
            patch("fastmcp.cli.run.logger") as mock_logger,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_server = MagicMock()
            mock_server.name = "test_server"
            mock_import.return_value = mock_server

            result = runner.invoke(cli.app, ["run", str(temp_python_file)])
            assert result.exit_code == 0
            mock_server.run.assert_called_once_with()
            mock_logger.debug.assert_called_with(
                f'Found server "test_server" in {temp_python_file}'
            )

    def test_run_command_with_transport(self, temp_python_file):
        """Test run command with transport option."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_server = MagicMock()
            mock_server.name = "test_server"
            mock_import.return_value = mock_server

            result = runner.invoke(
                cli.app, ["run", str(temp_python_file), "--transport", "sse"]
            )
            assert result.exit_code == 0
            mock_server.run.assert_called_once_with(transport="sse")

    def test_run_command_with_host(self, temp_python_file):
        """Test run command with host option."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_server = MagicMock()
            mock_server.name = "test_server"
            mock_import.return_value = mock_server

            result = runner.invoke(
                cli.app, ["run", str(temp_python_file), "--host", "0.0.0.0"]
            )
            assert result.exit_code == 0
            mock_server.run.assert_called_once_with(host="0.0.0.0")

    def test_run_command_with_port(self, temp_python_file):
        """Test run command with port option."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_server = MagicMock()
            mock_server.name = "test_server"
            mock_import.return_value = mock_server

            result = runner.invoke(
                cli.app, ["run", str(temp_python_file), "--port", "8080"]
            )
            assert result.exit_code == 0
            mock_server.run.assert_called_once_with(port=8080)

    def test_run_command_with_log_level(self, temp_python_file):
        """Test run command with log level option."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_server = MagicMock()
            mock_server.name = "test_server"
            mock_import.return_value = mock_server

            result = runner.invoke(
                cli.app, ["run", str(temp_python_file), "--log-level", "DEBUG"]
            )
            assert result.exit_code == 0
            mock_server.run.assert_called_once_with(log_level="DEBUG")

    def test_run_command_with_multiple_options(self, temp_python_file):
        """Test run command with multiple options."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_server = MagicMock()
            mock_server.name = "test_server"
            mock_import.return_value = mock_server

            result = runner.invoke(
                cli.app,
                [
                    "run",
                    str(temp_python_file),
                    "--transport",
                    "sse",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8080",
                    "--log-level",
                    "DEBUG",
                ],
            )
            assert result.exit_code == 0
            mock_server.run.assert_called_once_with(
                transport="sse", host="0.0.0.0", port=8080, log_level="DEBUG"
            )
