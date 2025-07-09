import subprocess
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
            str(editable_path),
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

    def test_version_command_execution(self):
        """Test that version command executes properly."""
        # The version command should execute without raising SystemExit
        command, bound, _ = app.parse_args(["version"])
        command()  # Should not raise

    def test_version_command_parsing(self):
        """Test that the version command parses arguments correctly."""
        command, bound, _ = app.parse_args(["version"])
        assert command.__name__ == "version"
        # Default arguments aren't included in bound.arguments
        assert bound.arguments == {}

    def test_version_command_with_copy_flag(self):
        """Test that the version command parses --copy flag correctly."""
        command, bound, _ = app.parse_args(["version", "--copy"])
        assert command.__name__ == "version"
        assert bound.arguments == {"copy": True}

    @patch("fastmcp.cli.cli.pyperclip.copy")
    @patch("fastmcp.cli.cli.console")
    def test_version_command_copy_functionality(
        self, mock_console, mock_pyperclip_copy
    ):
        """Test that the version command copies to clipboard when --copy is used."""
        command, bound, _ = app.parse_args(["version", "--copy"])
        command(**bound.arguments)

        # Verify pyperclip.copy was called with plain text format
        mock_pyperclip_copy.assert_called_once()
        copied_text = mock_pyperclip_copy.call_args[0][0]

        # Verify the copied text contains expected version info keys in plain text
        assert "FastMCP version:" in copied_text
        assert "MCP version:" in copied_text
        assert "Python version:" in copied_text
        assert "Platform:" in copied_text
        assert "FastMCP root path:" in copied_text

        # Verify no ANSI escape codes (terminal control characters)
        assert "\x1b[" not in copied_text
        mock_console.print.assert_called_with(
            "[green]✓[/green] Version information copied to clipboard"
        )


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

    def test_run_command_parsing_basic(self):
        """Test basic run command parsing."""
        command, bound, _ = app.parse_args(["run", "server.py"])

        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        # Cyclopts only includes non-default values
        assert "transport" not in bound.arguments
        assert "host" not in bound.arguments
        assert "port" not in bound.arguments
        assert "path" not in bound.arguments
        assert "log_level" not in bound.arguments
        assert "no_banner" not in bound.arguments

    def test_run_command_parsing_with_options(self):
        """Test run command parsing with various options."""
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
                "--path",
                "/v1/mcp",
                "--log-level",
                "DEBUG",
                "--no-banner",
            ]
        )

        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        assert bound.arguments["transport"] == "http"
        assert bound.arguments["host"] == "localhost"
        assert bound.arguments["port"] == 8080
        assert bound.arguments["path"] == "/v1/mcp"
        assert bound.arguments["log_level"] == "DEBUG"
        assert bound.arguments["no_banner"] is True

    def test_run_command_parsing_partial_options(self):
        """Test run command parsing with only some options."""
        command, bound, _ = app.parse_args(
            [
                "run",
                "server.py",
                "--transport",
                "http",
                "--no-banner",
            ]
        )

        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        assert bound.arguments["transport"] == "http"
        assert bound.arguments["no_banner"] is True
        # Other options should not be present
        assert "host" not in bound.arguments
        assert "port" not in bound.arguments
        assert "log_level" not in bound.arguments
        assert "path" not in bound.arguments

    def test_run_command_transport_aliases(self):
        """Test that both 'http' and 'streamable-http' are accepted as valid transport options."""
        # Test with 'http' transport
        command, bound, _ = app.parse_args(
            [
                "run",
                "server.py",
                "--transport",
                "http",
            ]
        )
        assert command is not None
        assert bound.arguments["transport"] == "http"

        # Test with 'streamable-http' transport
        command, bound, _ = app.parse_args(
            [
                "run",
                "server.py",
                "--transport",
                "streamable-http",
            ]
        )
        assert command is not None
        assert bound.arguments["transport"] == "streamable-http"


class TestWindowsSpecific:
    """Test Windows-specific functionality."""

    @patch("subprocess.run")
    def test_get_npx_command_windows_cmd(self, mock_run):
        """Test npx command detection on Windows with npx.cmd."""
        from fastmcp.cli.cli import _get_npx_command

        with patch("sys.platform", "win32"):
            # First call succeeds with npx.cmd
            mock_run.return_value = Mock(returncode=0)

            result = _get_npx_command()

            assert result == "npx.cmd"
            mock_run.assert_called_once_with(
                ["npx.cmd", "--version"],
                check=True,
                capture_output=True,
                shell=True,
            )

    @patch("subprocess.run")
    def test_get_npx_command_windows_exe(self, mock_run):
        """Test npx command detection on Windows with npx.exe."""
        from fastmcp.cli.cli import _get_npx_command

        with patch("sys.platform", "win32"):
            # First call fails, second succeeds
            mock_run.side_effect = [
                subprocess.CalledProcessError(1, "npx.cmd"),
                Mock(returncode=0),
            ]

            result = _get_npx_command()

            assert result == "npx.exe"
            assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_get_npx_command_windows_fallback(self, mock_run):
        """Test npx command detection on Windows with plain npx."""
        from fastmcp.cli.cli import _get_npx_command

        with patch("sys.platform", "win32"):
            # First two calls fail, third succeeds
            mock_run.side_effect = [
                subprocess.CalledProcessError(1, "npx.cmd"),
                subprocess.CalledProcessError(1, "npx.exe"),
                Mock(returncode=0),
            ]

            result = _get_npx_command()

            assert result == "npx"
            assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_get_npx_command_windows_not_found(self, mock_run):
        """Test npx command detection on Windows when npx is not found."""
        from fastmcp.cli.cli import _get_npx_command

        with patch("sys.platform", "win32"):
            # All calls fail
            mock_run.side_effect = subprocess.CalledProcessError(1, "npx")

            result = _get_npx_command()

            assert result is None
            assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_get_npx_command_unix(self, mock_run):
        """Test npx command detection on Unix systems."""
        from fastmcp.cli.cli import _get_npx_command

        with patch("sys.platform", "darwin"):
            result = _get_npx_command()

            assert result == "npx"
            mock_run.assert_not_called()

    def test_windows_path_parsing_with_colon(self, tmp_path):
        """Test parsing Windows paths with drive letters and colons."""
        from fastmcp.cli.run import parse_file_path

        # Create a real test file to test the logic
        test_file = tmp_path / "server.py"
        test_file.write_text("# test server")

        # Test normal file parsing (works on all platforms)
        file_path, obj = parse_file_path(str(test_file))
        assert obj is None

        # Test file:object parsing
        file_path, obj = parse_file_path(f"{test_file}:myapp")
        assert obj == "myapp"

        # Test that the file portion resolves correctly when object is specified
        assert file_path == test_file.resolve()


class TestInspectCommand:
    """Test the inspect command."""

    def test_inspect_command_parsing_basic(self):
        """Test basic inspect command parsing."""
        command, bound, _ = app.parse_args(["inspect", "server.py"])

        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        # Only explicitly set parameters are in bound.arguments
        assert "output" not in bound.arguments

    def test_inspect_command_parsing_with_output(self, tmp_path):
        """Test inspect command parsing with output file."""
        output_file = tmp_path / "output.json"

        command, bound, _ = app.parse_args(
            [
                "inspect",
                "server.py",
                "--output",
                str(output_file),
            ]
        )

        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        # Output is parsed as a Path object
        assert bound.arguments["output"] == output_file

    async def test_inspect_command_with_real_server(self, tmp_path):
        """Test inspect command with a real server file."""
        # Create a real server file
        server_file = tmp_path / "test_server.py"
        server_file.write_text("""
import fastmcp

mcp = fastmcp.FastMCP("InspectTestServer")

@mcp.tool
def test_tool(x: int) -> int:
    return x * 2

@mcp.prompt
def test_prompt(name: str) -> str:
    return f"Hello, {name}!"
""")

        output_file = tmp_path / "inspect_output.json"

        # Parse and execute the command
        command, bound, _ = app.parse_args(
            [
                "inspect",
                str(server_file),
                "--output",
                str(output_file),
            ]
        )

        await command(**bound.arguments)

        # Verify the output file was created and contains expected content
        assert output_file.exists()
        content = output_file.read_text()

        # Basic checks that the inspection worked
        assert "InspectTestServer" in content
        assert "test_tool" in content
        assert "test_prompt" in content
