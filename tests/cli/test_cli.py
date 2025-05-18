"""Tests for the CLI module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from typer.testing import CliRunner

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
    """Tests for helper functions in cli.py."""

    def test_get_npx_command_unix(self):
        """Test getting npx command on unix systems."""
        with patch("sys.platform", "linux"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0)
                assert cli._get_npx_command() == "npx"

    def test_get_npx_command_windows(self):
        """Test getting npx command on Windows."""
        with patch("sys.platform", "win32"):
            with patch("subprocess.run") as mock_run:
                # First try fails, second succeeds
                mock_run.side_effect = [
                    subprocess.CalledProcessError(1, "npx.cmd"),
                    Mock(returncode=0),
                ]
                assert cli._get_npx_command() == "npx.exe"

    def test_get_npx_command_not_found(self):
        """Test when npx command is not found."""
        with patch("sys.platform", "win32"):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = [
                    subprocess.CalledProcessError(1, "npx.cmd"),
                    subprocess.CalledProcessError(1, "npx.exe"),
                    subprocess.CalledProcessError(1, "npx"),
                ]
                assert cli._get_npx_command() is None

    def test_parse_env_var_valid(self):
        """Test parsing valid environment variables."""
        assert cli._parse_env_var("KEY=VALUE") == ("KEY", "VALUE")
        assert cli._parse_env_var("KEY=") == ("KEY", "")
        assert cli._parse_env_var("KEY=VALUE=WITH=EQUALS") == (
            "KEY",
            "VALUE=WITH=EQUALS",
        )
        assert cli._parse_env_var(" KEY = VALUE ") == ("KEY", "VALUE")

    def test_build_uv_command_basic(self):
        """Test building basic uv command."""
        cmd = cli._build_uv_command("file.py")
        assert cmd == ["uv", "run", "--with", "fastmcp", "fastmcp", "run", "file.py"]

    def test_build_uv_command_with_editable(self):
        """Test building uv command with editable flag."""
        project_path = Path("/path/to/project")
        cmd = cli._build_uv_command("file.py", with_editable=project_path)
        assert cmd == [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "--with-editable",
            str(project_path),
            "fastmcp",
            "run",
            "file.py",
        ]

    def test_build_uv_command_with_packages(self):
        """Test building uv command with additional packages."""
        cmd = cli._build_uv_command("file.py", with_packages=["pkg1", "pkg2"])
        assert cmd == [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "--with",
            "pkg1",
            "--with",
            "pkg2",
            "fastmcp",
            "run",
            "file.py",
        ]

    def test_build_uv_command_full(self):
        """Test building full uv command with all options."""
        project_path = Path("/path/to/project")
        cmd = cli._build_uv_command(
            "file.py:server",
            with_editable=project_path,
            with_packages=["pkg1", "pkg2"],
        )
        assert cmd == [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "--with-editable",
            str(project_path),
            "--with",
            "pkg1",
            "--with",
            "pkg2",
            "fastmcp",
            "run",
            "file.py:server",
        ]


class TestVersionCommand:
    """Tests for the version command."""

    def test_version_early_exit_with_resilient_parsing(self):
        """Test version command exits early with resilient parsing."""
        ctx = MagicMock()
        ctx.resilient_parsing = True
        result = cli.version(ctx)
        assert result is None


class TestDevCommand:
    """Tests for the dev command."""

    def test_dev_command_success(self, temp_python_file, mock_logger):
        """Test successful dev command execution."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
            patch("fastmcp.cli.cli._get_npx_command") as mock_get_npx,
            patch("fastmcp.cli.cli._build_uv_command") as mock_build_uv,
            patch("subprocess.run") as mock_run,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_server = MagicMock()
            mock_server.dependencies = ["extra_dep"]
            mock_import.return_value = mock_server
            mock_get_npx.return_value = "npx"
            mock_build_uv.return_value = ["uv", "command"]
            mock_run.return_value = MagicMock(returncode=0)

            result = runner.invoke(cli.app, ["dev", str(temp_python_file)])
            assert result.exit_code == 0
            mock_run.assert_called_once()

            # Check dependencies were passed correctly
            mock_build_uv.assert_called_once_with(
                str(temp_python_file), None, ["extra_dep"]
            )

    def test_dev_command_with_ui_port(self, temp_python_file):
        """Test dev command with UI port."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
            patch("fastmcp.cli.cli._get_npx_command") as mock_get_npx,
            patch("fastmcp.cli.cli._build_uv_command") as mock_build_uv,
            patch("subprocess.run") as mock_run,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_import.return_value = MagicMock(dependencies=[])
            mock_get_npx.return_value = "npx"
            mock_build_uv.return_value = ["uv", "command"]
            mock_run.return_value = MagicMock(returncode=0)

            result = runner.invoke(
                cli.app, ["dev", str(temp_python_file), "--ui-port", "3000"]
            )
            assert result.exit_code == 0

            # Check environment variables were set
            env = mock_run.call_args[1]["env"]
            assert "CLIENT_PORT" in env
            assert env["CLIENT_PORT"] == "3000"

    def test_dev_command_with_server_port(self, temp_python_file):
        """Test dev command with server port."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
            patch("fastmcp.cli.cli._get_npx_command") as mock_get_npx,
            patch("fastmcp.cli.cli._build_uv_command") as mock_build_uv,
            patch("subprocess.run") as mock_run,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_import.return_value = MagicMock(dependencies=[])
            mock_get_npx.return_value = "npx"
            mock_build_uv.return_value = ["uv", "command"]
            mock_run.return_value = MagicMock(returncode=0)

            result = runner.invoke(
                cli.app, ["dev", str(temp_python_file), "--server-port", "8080"]
            )
            assert result.exit_code == 0

            # Check environment variables were set
            env = mock_run.call_args[1]["env"]
            assert "SERVER_PORT" in env
            assert env["SERVER_PORT"] == "8080"

    def test_dev_command_inspector_version(self, temp_python_file):
        """Test dev command with specific inspector version."""
        with (
            patch("fastmcp.cli.run.parse_file_path") as mock_parse,
            patch("fastmcp.cli.run.import_server") as mock_import,
            patch("fastmcp.cli.cli._get_npx_command") as mock_get_npx,
            patch("fastmcp.cli.cli._build_uv_command") as mock_build_uv,
            patch("subprocess.run") as mock_run,
        ):
            mock_parse.return_value = (temp_python_file, None)
            mock_import.return_value = MagicMock(dependencies=[])
            mock_get_npx.return_value = "npx"
            mock_build_uv.return_value = ["uv", "command"]
            mock_run.return_value = MagicMock(returncode=0)

            result = runner.invoke(
                cli.app, ["dev", str(temp_python_file), "--inspector-version", "1.0.0"]
            )
            assert result.exit_code == 0

            # Check inspector version was used
            inspector_cmd = mock_run.call_args[0][0][1]
            assert inspector_cmd == "@modelcontextprotocol/inspector@1.0.0"


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
