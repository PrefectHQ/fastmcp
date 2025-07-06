"""Tests for Cursor CLI integration."""

import base64
import json
from pathlib import Path
from unittest.mock import patch

from fastmcp.cli.install.cursor import (
    generate_cursor_deeplink,
    install_cursor,
    open_deeplink,
)
from fastmcp.mcp_config import StdioMCPServer


class TestGenerateCursorDeeplink:
    """Test generate_cursor_deeplink function."""

    def test_generates_valid_deeplink(self):
        """Should generate a valid Cursor deeplink with base64 encoded config."""
        server_config = StdioMCPServer(
            command="uv",
            args=["run", "--with", "fastmcp", "fastmcp", "run", "server.py"],
            env={"API_KEY": "secret"},
        )

        deeplink = generate_cursor_deeplink("test-server", server_config)

        assert deeplink.startswith("cursor://anysphere.cursor-deeplink/mcp/install?")
        assert "name=test-server" in deeplink
        assert "config=" in deeplink

    def test_config_is_url_safe_base64(self):
        """Should use URL-safe base64 encoding for the config."""
        server_config = StdioMCPServer(
            command="test",
            args=["arg1", "arg2"],
        )

        deeplink = generate_cursor_deeplink("test", server_config)

        # Extract the config parameter
        config_param = deeplink.split("config=")[1]

        # Should be decodable as URL-safe base64
        decoded = base64.urlsafe_b64decode(config_param.encode())
        config_data = json.loads(decoded)

        assert config_data["command"] == "test"
        assert config_data["args"] == ["arg1", "arg2"]

    def test_excludes_none_values(self):
        """Should exclude None values from the configuration."""
        server_config = StdioMCPServer(
            command="test",
            args=["arg1"],
            timeout=None,  # This should be excluded
        )

        deeplink = generate_cursor_deeplink("test", server_config)
        config_param = deeplink.split("config=")[1]
        decoded = base64.urlsafe_b64decode(config_param.encode())
        config_data = json.loads(decoded)

        assert "timeout" not in config_data


class TestOpenDeeplink:
    """Test open_deeplink function."""

    @patch("subprocess.run")
    @patch("fastmcp.cli.install.cursor.sys.platform", "darwin")
    def test_opens_on_macos(self, mock_run):
        """Should use 'open' command on macOS."""
        mock_run.return_value = None

        result = open_deeplink("cursor://test")

        assert result is True
        mock_run.assert_called_once_with(
            ["open", "cursor://test"], check=True, capture_output=True
        )

    @patch("subprocess.run")
    @patch("fastmcp.cli.install.cursor.sys.platform", "win32")
    def test_opens_on_windows(self, mock_run):
        """Should use 'start' command on Windows."""
        mock_run.return_value = None

        result = open_deeplink("cursor://test")

        assert result is True
        mock_run.assert_called_once_with(
            ["start", "cursor://test"], shell=True, check=True, capture_output=True
        )

    @patch("subprocess.run")
    @patch("fastmcp.cli.install.cursor.sys.platform", "linux")
    def test_opens_on_linux(self, mock_run):
        """Should use 'xdg-open' command on Linux."""
        mock_run.return_value = None

        result = open_deeplink("cursor://test")

        assert result is True
        mock_run.assert_called_once_with(
            ["xdg-open", "cursor://test"], check=True, capture_output=True
        )

    @patch("subprocess.run")
    def test_handles_subprocess_error(self, mock_run):
        """Should return False when subprocess command fails."""
        from subprocess import CalledProcessError

        mock_run.side_effect = CalledProcessError(1, "open")

        result = open_deeplink("cursor://test")

        assert result is False

    @patch("subprocess.run")
    def test_handles_file_not_found(self, mock_run):
        """Should return False when command is not found."""
        mock_run.side_effect = FileNotFoundError()

        result = open_deeplink("cursor://test")

        assert result is False


class TestInstallCursor:
    """Test install_cursor function."""

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.generate_cursor_deeplink")
    @patch("fastmcp.cli.install.cursor.print")
    def test_successful_installation(
        self, mock_print, mock_generate_deeplink, mock_open_deeplink
    ):
        """Should successfully install when deeplink opens."""
        mock_generate_deeplink.return_value = "cursor://test-deeplink"
        mock_open_deeplink.return_value = True

        result = install_cursor(Path("server.py"), None, "test-server")

        assert result is True
        mock_generate_deeplink.assert_called_once()
        mock_open_deeplink.assert_called_once_with("cursor://test-deeplink")
        mock_print.assert_called_once()
        # Check that the success message was printed
        assert "Opening Cursor to install" in str(mock_print.call_args)

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.generate_cursor_deeplink")
    @patch("fastmcp.cli.install.cursor.print")
    def test_fallback_when_deeplink_fails(
        self, mock_print, mock_generate_deeplink, mock_open_deeplink
    ):
        """Should provide manual link when deeplink fails to open."""
        mock_generate_deeplink.return_value = "cursor://test-deeplink"
        mock_open_deeplink.return_value = False

        result = install_cursor(Path("server.py"), None, "test-server")

        assert result is True
        assert mock_print.call_count == 2
        # Check that both error and manual link messages were printed
        print_calls = [str(call) for call in mock_print.call_args_list]
        assert any(
            "Could not open Cursor automatically" in call for call in print_calls
        )
        assert any("Please open this link" in call for call in print_calls)

    @patch("fastmcp.cli.install.cursor.generate_cursor_deeplink")
    @patch("fastmcp.cli.install.cursor.print")
    def test_handles_deeplink_generation_error(
        self, mock_print, mock_generate_deeplink
    ):
        """Should return False when deeplink generation fails."""
        mock_generate_deeplink.side_effect = Exception("Test error")

        result = install_cursor(Path("server.py"), None, "test-server")

        assert result is False
        mock_print.assert_called_once()
        assert "Failed to generate Cursor deeplink" in str(mock_print.call_args)

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.generate_cursor_deeplink")
    def test_builds_correct_server_config(
        self, mock_generate_deeplink, mock_open_deeplink
    ):
        """Should build correct server configuration with all options."""
        mock_generate_deeplink.return_value = "cursor://test"
        mock_open_deeplink.return_value = True

        install_cursor(
            file=Path("server.py"),
            server_object="custom_server",
            name="test-server",
            with_editable=Path("/path/to/editable"),
            with_packages=["pandas", "requests"],
            env_vars={"API_KEY": "secret", "DEBUG": "true"},
        )

        # Check that generate_cursor_deeplink was called with correct config
        call_args = mock_generate_deeplink.call_args
        server_name, server_config = call_args[0]

        assert server_name == "test-server"
        assert server_config.command == "uv"
        assert "run" in server_config.args
        assert "--with" in server_config.args
        assert "fastmcp" in server_config.args
        assert "pandas" in server_config.args
        assert "requests" in server_config.args
        assert "--with-editable" in server_config.args
        assert str(Path("/path/to/editable")) in server_config.args
        assert "fastmcp" in server_config.args
        assert "run" in server_config.args
        assert server_config.env == {"API_KEY": "secret", "DEBUG": "true"}

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.generate_cursor_deeplink")
    def test_resolves_absolute_paths(self, mock_generate_deeplink, mock_open_deeplink):
        """Should resolve server spec to absolute path."""
        mock_generate_deeplink.return_value = "cursor://test"
        mock_open_deeplink.return_value = True

        install_cursor(Path("server.py"), None, "test-server")

        call_args = mock_generate_deeplink.call_args
        _, server_config = call_args[0]

        # Find the server spec after "fastmcp run"
        server_spec_in_args = None
        for i, arg in enumerate(server_config.args):
            if (
                arg == "fastmcp"
                and i + 2 < len(server_config.args)
                and server_config.args[i + 1] == "run"
            ):
                server_spec_in_args = server_config.args[i + 2]
                break

        assert server_spec_in_args is not None
        assert str(Path("server.py").resolve()) in server_spec_in_args

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.generate_cursor_deeplink")
    def test_handles_server_spec_with_object(
        self, mock_generate_deeplink, mock_open_deeplink
    ):
        """Should correctly handle server spec with object notation."""
        mock_generate_deeplink.return_value = "cursor://test"
        mock_open_deeplink.return_value = True

        install_cursor(Path("server.py"), "custom_object", "test-server")

        call_args = mock_generate_deeplink.call_args
        _, server_config = call_args[0]

        # Find the server spec after "fastmcp run"
        server_spec_in_args = None
        for i, arg in enumerate(server_config.args):
            if (
                arg == "fastmcp"
                and i + 2 < len(server_config.args)
                and server_config.args[i + 1] == "run"
            ):
                server_spec_in_args = server_config.args[i + 2]
                break

        assert server_spec_in_args is not None
        assert ":custom_object" in server_spec_in_args
        assert str(Path("server.py").resolve()) in server_spec_in_args
