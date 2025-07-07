import base64
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fastmcp.cli.install.cursor import (
    cursor_command,
    generate_cursor_deeplink,
    install_cursor,
    open_deeplink,
)
from fastmcp.mcp_config import StdioMCPServer


class TestCursorDeeplinkGeneration:
    """Test cursor deeplink generation functionality."""

    def test_generate_deeplink_basic(self):
        """Test basic deeplink generation."""
        server_config = StdioMCPServer(
            command="uv",
            args=["run", "--with", "fastmcp", "fastmcp", "run", "server.py"],
        )

        deeplink = generate_cursor_deeplink("test-server", server_config)

        assert deeplink.startswith("cursor://anysphere.cursor-deeplink/mcp/install?")
        assert "name=test-server" in deeplink
        assert "config=" in deeplink

        # Verify base64 encoding
        config_part = deeplink.split("config=")[1]
        decoded = base64.urlsafe_b64decode(config_part).decode()
        config_data = json.loads(decoded)

        assert config_data["command"] == "uv"
        assert config_data["args"] == [
            "run",
            "--with",
            "fastmcp",
            "fastmcp",
            "run",
            "server.py",
        ]

    def test_generate_deeplink_with_env_vars(self):
        """Test deeplink generation with environment variables."""
        server_config = StdioMCPServer(
            command="uv",
            args=["run", "--with", "fastmcp", "fastmcp", "run", "server.py"],
            env={"API_KEY": "secret123", "DEBUG": "true"},
        )

        deeplink = generate_cursor_deeplink("my-server", server_config)

        # Decode and verify
        config_part = deeplink.split("config=")[1]
        decoded = base64.urlsafe_b64decode(config_part).decode()
        config_data = json.loads(decoded)

        assert config_data["env"] == {"API_KEY": "secret123", "DEBUG": "true"}

    def test_generate_deeplink_special_characters(self):
        """Test deeplink generation with special characters in server name."""
        server_config = StdioMCPServer(
            command="uv",
            args=["run", "--with", "fastmcp", "fastmcp", "run", "server.py"],
        )

        # Test with spaces and special chars in name
        deeplink = generate_cursor_deeplink("my server (test)", server_config)

        assert (
            "name=my%20server%20%28test%29" in deeplink
            or "name=my server (test)" in deeplink
        )

    def test_generate_deeplink_empty_config(self):
        """Test deeplink generation with minimal config."""
        server_config = StdioMCPServer(command="python", args=["server.py"])

        deeplink = generate_cursor_deeplink("minimal", server_config)

        config_part = deeplink.split("config=")[1]
        decoded = base64.urlsafe_b64decode(config_part).decode()
        config_data = json.loads(decoded)

        assert config_data["command"] == "python"
        assert config_data["args"] == ["server.py"]
        assert config_data["env"] == {}  # Empty env dict is included

    def test_generate_deeplink_complex_args(self):
        """Test deeplink generation with complex arguments."""
        server_config = StdioMCPServer(
            command="uv",
            args=[
                "run",
                "--with",
                "fastmcp",
                "--with",
                "numpy>=1.20",
                "--with-editable",
                "/path/to/local/package",
                "fastmcp",
                "run",
                "server.py:CustomServer",
            ],
        )

        deeplink = generate_cursor_deeplink("complex-server", server_config)

        config_part = deeplink.split("config=")[1]
        decoded = base64.urlsafe_b64decode(config_part).decode()
        config_data = json.loads(decoded)

        assert "--with-editable" in config_data["args"]
        assert "server.py:CustomServer" in config_data["args"]


class TestOpenDeeplink:
    """Test deeplink opening functionality."""

    @patch("subprocess.run")
    def test_open_deeplink_macos(self, mock_run):
        """Test opening deeplink on macOS."""
        with patch("sys.platform", "darwin"):
            mock_run.return_value = Mock(returncode=0)

            result = open_deeplink("cursor://test")

            assert result is True
            mock_run.assert_called_once_with(
                ["open", "cursor://test"], check=True, capture_output=True
            )

    @patch("subprocess.run")
    def test_open_deeplink_windows(self, mock_run):
        """Test opening deeplink on Windows."""
        with patch("sys.platform", "win32"):
            mock_run.return_value = Mock(returncode=0)

            result = open_deeplink("cursor://test")

            assert result is True
            mock_run.assert_called_once_with(
                ["start", "cursor://test"], shell=True, check=True, capture_output=True
            )

    @patch("subprocess.run")
    def test_open_deeplink_linux(self, mock_run):
        """Test opening deeplink on Linux."""
        with patch("sys.platform", "linux"):
            mock_run.return_value = Mock(returncode=0)

            result = open_deeplink("cursor://test")

            assert result is True
            mock_run.assert_called_once_with(
                ["xdg-open", "cursor://test"], check=True, capture_output=True
            )

    @patch("subprocess.run")
    def test_open_deeplink_failure(self, mock_run):
        """Test handling of deeplink opening failure."""
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(1, ["open"])

        result = open_deeplink("cursor://test")

        assert result is False

    @patch("subprocess.run")
    def test_open_deeplink_command_not_found(self, mock_run):
        """Test handling when open command is not found."""
        mock_run.side_effect = FileNotFoundError()

        result = open_deeplink("cursor://test")

        assert result is False


class TestInstallCursor:
    """Test cursor installation functionality."""

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.print")
    def test_install_cursor_success(self, mock_print, mock_open_deeplink):
        """Test successful cursor installation."""
        mock_open_deeplink.return_value = True

        result = install_cursor(
            file=Path("/path/to/server.py"),
            server_object=None,
            name="test-server",
        )

        assert result is True
        mock_open_deeplink.assert_called_once()
        # Verify the deeplink was generated correctly
        call_args = mock_open_deeplink.call_args[0][0]
        assert call_args.startswith("cursor://anysphere.cursor-deeplink/mcp/install?")
        assert "name=test-server" in call_args

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.print")
    def test_install_cursor_with_packages(self, mock_print, mock_open_deeplink):
        """Test cursor installation with additional packages."""
        mock_open_deeplink.return_value = True

        result = install_cursor(
            file=Path("/path/to/server.py"),
            server_object="app",
            name="test-server",
            with_packages=["numpy", "pandas"],
            env_vars={"API_KEY": "test"},
        )

        assert result is True
        call_args = mock_open_deeplink.call_args[0][0]

        # Decode the config to verify packages
        config_part = call_args.split("config=")[1]
        decoded = base64.urlsafe_b64decode(config_part).decode()
        config_data = json.loads(decoded)

        # Check that all packages are included
        assert "--with" in config_data["args"]
        assert "numpy" in config_data["args"]
        assert "pandas" in config_data["args"]
        assert "fastmcp" in config_data["args"]
        assert config_data["env"] == {"API_KEY": "test"}

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.print")
    def test_install_cursor_with_editable(self, mock_print, mock_open_deeplink):
        """Test cursor installation with editable package."""
        mock_open_deeplink.return_value = True

        result = install_cursor(
            file=Path("/path/to/server.py"),
            server_object="custom_app",
            name="test-server",
            with_editable=Path("/local/package"),
        )

        assert result is True
        call_args = mock_open_deeplink.call_args[0][0]

        # Decode and verify editable path
        config_part = call_args.split("config=")[1]
        decoded = base64.urlsafe_b64decode(config_part).decode()
        config_data = json.loads(decoded)

        assert "--with-editable" in config_data["args"]
        # Check for the editable path in a platform-agnostic way
        editable_path_str = str(Path("/local/package"))
        assert editable_path_str in config_data["args"]
        assert "server.py:custom_app" in " ".join(config_data["args"])

    @patch("fastmcp.cli.install.cursor.open_deeplink")
    @patch("fastmcp.cli.install.cursor.print")
    def test_install_cursor_failure(self, mock_print, mock_open_deeplink):
        """Test cursor installation when deeplink fails to open."""
        mock_open_deeplink.return_value = False

        result = install_cursor(
            file=Path("/path/to/server.py"),
            server_object=None,
            name="test-server",
        )

        assert result is False
        # Verify failure message was printed
        mock_print.assert_called()

    def test_install_cursor_deduplicate_packages(self):
        """Test that duplicate packages are deduplicated."""
        with patch("fastmcp.cli.install.cursor.open_deeplink") as mock_open:
            mock_open.return_value = True

            install_cursor(
                file=Path("/path/to/server.py"),
                server_object=None,
                name="test-server",
                with_packages=["numpy", "fastmcp", "numpy", "pandas", "fastmcp"],
            )

            call_args = mock_open.call_args[0][0]
            config_part = call_args.split("config=")[1]
            decoded = base64.urlsafe_b64decode(config_part).decode()
            config_data = json.loads(decoded)

            # Count occurrences of each package
            args_str = " ".join(config_data["args"])
            assert args_str.count("numpy") == 1
            assert args_str.count("pandas") == 1
            # fastmcp appears twice: once as --with fastmcp and once as the command
            assert args_str.count("fastmcp") == 2


class TestCursorCommand:
    """Test the cursor CLI command."""

    @patch("fastmcp.cli.install.cursor.install_cursor")
    @patch("fastmcp.cli.install.cursor.process_common_args")
    def test_cursor_command_basic(self, mock_process_args, mock_install):
        """Test basic cursor command execution."""
        mock_process_args.return_value = (
            Path("server.py"),
            None,
            "test-server",
            [],
            {},
        )
        mock_install.return_value = True

        with patch("sys.exit") as mock_exit:
            cursor_command("server.py")

        mock_install.assert_called_once_with(
            file=Path("server.py"),
            server_object=None,
            name="test-server",
            with_editable=None,
            with_packages=[],
            env_vars={},
        )
        mock_exit.assert_not_called()

    @patch("fastmcp.cli.install.cursor.install_cursor")
    @patch("fastmcp.cli.install.cursor.process_common_args")
    def test_cursor_command_failure(self, mock_process_args, mock_install):
        """Test cursor command when installation fails."""
        mock_process_args.return_value = (
            Path("server.py"),
            None,
            "test-server",
            [],
            {},
        )
        mock_install.return_value = False

        with pytest.raises(SystemExit) as exc_info:
            cursor_command("server.py")

        assert exc_info.value.code == 1
