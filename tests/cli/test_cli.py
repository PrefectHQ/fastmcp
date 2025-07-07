"""Tests for the main CLI functionality."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fastmcp.cli.cli import _build_uv_command, _parse_env_var, app


class TestMainCLI:
    """Test the main CLI application."""

    def test_app_exists(self):
        """Test that the main app is properly configured."""
        # app.name is a tuple in cyclopts
        assert "fastmcp" in app.name
        assert "FastMCP 2.0" in app.help
        # Just check that version exists, not the specific value
        assert hasattr(app, "version")

    def test_parse_env_var_valid(self):
        """Test parsing valid environment variables."""
        key, value = _parse_env_var("KEY=value")
        assert key == "KEY"
        assert value == "value"

        key, value = _parse_env_var("COMPLEX_KEY=complex=value=with=equals")
        assert key == "COMPLEX_KEY"
        assert value == "complex=value=with=equals"

    def test_parse_env_var_invalid(self):
        """Test parsing invalid environment variables exits."""
        with pytest.raises(SystemExit) as exc_info:
            _parse_env_var("INVALID_FORMAT")
        assert exc_info.value.code == 1

    def test_build_uv_command_basic(self):
        """Test building basic uv command."""
        cmd = _build_uv_command("server.py")
        expected = ["uv", "run", "--with", "fastmcp", "fastmcp", "run", "server.py"]
        assert cmd == expected

    def test_build_uv_command_with_editable(self):
        """Test building uv command with editable package."""
        editable_path = Path("/path/to/package")
        cmd = _build_uv_command("server.py", with_editable=editable_path)
        expected = [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "--with-editable",
            "/path/to/package",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_command_with_packages(self):
        """Test building uv command with additional packages."""
        cmd = _build_uv_command("server.py", with_packages=["pkg1", "pkg2"])
        expected = [
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
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_command_no_banner(self):
        """Test building uv command with no banner flag."""
        cmd = _build_uv_command("server.py", no_banner=True)
        expected = [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "fastmcp",
            "run",
            "server.py",
            "--no-banner",
        ]
        assert cmd == expected


class TestVersionCommand:
    """Test the version command."""

    @patch("fastmcp.cli.cli.sys.exit")
    @patch("fastmcp.cli.cli.console.print")
    def test_version_command(self, mock_print, mock_exit):
        """Test that version command prints info and exits."""
        # Parse and execute version command
        command, bound, _ = app.parse_args(["version"])
        command()

        # Verify it printed something and exited with 0
        mock_print.assert_called_once()
        mock_exit.assert_called_once_with(0)


class TestDevCommand:
    """Test the dev command."""

    def test_dev_command_parsing(self):
        """Test that dev command can be parsed with various options."""
        # Test basic parsing
        command, bound, _ = app.parse_args(["dev", "server.py"])
        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"

        # Test with options
        command, bound, _ = app.parse_args(
            [
                "dev",
                "server.py",
                "--with",
                "package1",
                "--inspector-version",
                "1.0.0",
                "--ui-port",
                "3000",
            ]
        )
        assert bound.arguments["with_packages"] == ["package1"]
        assert bound.arguments["inspector_version"] == "1.0.0"
        assert bound.arguments["ui_port"] == 3000


class TestRunCommand:
    """Test the run command."""

    @patch("fastmcp.cli.cli.run_module.run_command")
    def test_run_command_basic(self, mock_run_command):
        """Test basic run command."""
        command, bound, _ = app.parse_args(["run", "server.py"])
        command(**bound.arguments)

        mock_run_command.assert_called_once_with(
            server_spec="server.py",
            transport=None,
            host=None,
            port=None,
            log_level=None,
            server_args=[],
            show_banner=True,
        )

    @patch("fastmcp.cli.cli.run_module.run_command")
    def test_run_command_with_options(self, mock_run_command):
        """Test run command with various options."""
        command, bound, _ = app.parse_args(
            [
                "run",
                "server.py",
                "--transport",
                "http",
                "--host",
                "localhost",
                "--port",
                "8080",
                "--log-level",
                "DEBUG",
                "--no-banner",
            ]
        )
        command(**bound.arguments)

        mock_run_command.assert_called_once_with(
            server_spec="server.py",
            transport="http",
            host="localhost",
            port=8080,
            log_level="DEBUG",
            server_args=[],
            show_banner=False,
        )

    @patch("fastmcp.cli.cli.run_module.run_command")
    def test_run_command_failure(self, mock_run_command):
        """Test run command handling failures."""
        mock_run_command.side_effect = Exception("Test error")

        with pytest.raises(SystemExit) as exc_info:
            command, bound, _ = app.parse_args(["run", "server.py"])
            command(**bound.arguments)

        assert exc_info.value.code == 1


class TestInspectCommand:
    """Test the inspect command."""

    @patch("fastmcp.cli.cli.run_module.parse_file_path")
    @patch("fastmcp.cli.cli.run_module.import_server")
    @patch("fastmcp.cli.cli.inspect_fastmcp")
    def test_inspect_command_basic(
        self, mock_inspect, mock_import_server, mock_parse_file_path, tmp_path
    ):
        """Test basic inspect command functionality."""
        # Setup mocks
        mock_parse_file_path.return_value = (Path("server.py"), None)
        mock_server = Mock()
        mock_import_server.return_value = mock_server

        mock_info = Mock()
        mock_info.name = "TestServer"
        mock_info.tools = []
        mock_info.prompts = []
        mock_info.resources = []
        mock_info.templates = []
        mock_inspect.return_value = mock_info

        # Mock TypeAdapter
        with patch("fastmcp.cli.cli.TypeAdapter") as mock_adapter:
            mock_adapter.return_value.dump_json.return_value = b'{"name": "TestServer"}'

            output_file = tmp_path / "test-output.json"

            # Parse and execute
            command, bound, _ = app.parse_args(
                [
                    "inspect",
                    "server.py",
                    "--output",
                    str(output_file),
                ]
            )

            # This is an async command, so we need to run it
            import asyncio

            asyncio.run(command(**bound.arguments))

        # Verify the output file was created
        assert output_file.exists()
        assert output_file.read_text() == '{"name": "TestServer"}'

    @patch("fastmcp.cli.cli.run_module.import_server")
    def test_inspect_command_failure(self, mock_import_server):
        """Test inspect command handling failures."""
        mock_import_server.side_effect = Exception("Import failed")

        with pytest.raises(SystemExit) as exc_info:
            command, bound, _ = app.parse_args(["inspect", "server.py"])
            import asyncio

            asyncio.run(command(**bound.arguments))

        assert exc_info.value.code == 1
