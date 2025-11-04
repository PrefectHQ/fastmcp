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

        # Test with spaces and special chars in name - should be URL encoded
        deeplink = generate_cursor_deeplink("my server (test)", server_config)

        # Spaces and parentheses must be URL-encoded
        assert "name=my%20server%20%28test%29" in deeplink
        # Ensure no unencoded version appears
        assert "name=my server (test)" not in deeplink

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

    def test_generate_deeplink_url_injection_protection(self):
        """Test that special characters in server name are properly URL-encoded to prevent injection."""
        server_config = StdioMCPServer(
            command="python",
            args=["server.py"],
        )

        # Test the PoC case from the security advisory
        deeplink = generate_cursor_deeplink("test&calc", server_config)

        # The & should be encoded as %26, preventing it from being interpreted as a query parameter separator
        assert "name=test%26calc" in deeplink
        assert "name=test&calc" not in deeplink

        # Verify the URL structure is intact
        assert deeplink.startswith("cursor://anysphere.cursor-deeplink/mcp/install?")
        assert deeplink.count("&") == 1  # Only one & between name and config parameters

        # Test other potentially dangerous characters
        dangerous_names = [
            ("test|calc", "test%7Ccalc"),
            ("test;calc", "test%3Bcalc"),
            ("test<calc", "test%3Ccalc"),
            ("test>calc", "test%3Ecalc"),
            ("test`calc", "test%60calc"),
            ("test$calc", "test%24calc"),
            ("test'calc", "test%27calc"),
            ('test"calc', "test%22calc"),
            ("test calc", "test%20calc"),
            ("test#anchor", "test%23anchor"),
            ("test?query=val", "test%3Fquery%3Dval"),
        ]

        for dangerous_name, expected_encoded in dangerous_names:
            deeplink = generate_cursor_deeplink(dangerous_name, server_config)
            assert f"name={expected_encoded}" in deeplink, (
                f"Failed to encode {dangerous_name}"
            )
            # Ensure no unencoded special chars that could break URL structure
            name_part = deeplink.split("name=")[1].split("&")[0]
            assert name_part == expected_encoded


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

    def test_open_deeplink_windows(self):
        """Test opening deeplink on Windows."""
        with patch("sys.platform", "win32"):
            with patch(
                "fastmcp.cli.install.cursor.os.startfile", create=True
            ) as mock_startfile:
                result = open_deeplink("cursor://test")

                assert result is True
                mock_startfile.assert_called_once_with("cursor://test")

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

        with patch("sys.platform", "darwin"):
            mock_run.side_effect = subprocess.CalledProcessError(1, ["open"])

            result = open_deeplink("cursor://test")

            assert result is False

    @patch("subprocess.run")
    def test_open_deeplink_command_not_found(self, mock_run):
        """Test handling when open command is not found."""
        with patch("sys.platform", "darwin"):
            mock_run.side_effect = FileNotFoundError()

            result = open_deeplink("cursor://test")

            assert result is False

    def test_open_deeplink_invalid_scheme(self):
        """Test that non-cursor:// URLs are rejected."""
        result = open_deeplink("http://malicious.com")
        assert result is False

        result = open_deeplink("https://example.com")
        assert result is False

        result = open_deeplink("file:///etc/passwd")
        assert result is False

    def test_open_deeplink_valid_cursor_scheme(self):
        """Test that cursor:// URLs are accepted."""
        with patch("sys.platform", "darwin"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0)
                result = open_deeplink("cursor://anysphere.cursor-deeplink/mcp/install")
                assert result is True

    def test_open_deeplink_empty_url(self):
        """Test handling of empty URL."""
        result = open_deeplink("")
        assert result is False

    def test_open_deeplink_windows_oserror(self):
        """Test handling of OSError on Windows."""
        with patch("sys.platform", "win32"):
            with patch(
                "fastmcp.cli.install.cursor.os.startfile", create=True
            ) as mock_startfile:
                mock_startfile.side_effect = OSError("File not found")
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

        # Use an absolute path that works on all platforms
        editable_path = Path.cwd() / "local" / "package"

        result = install_cursor(
            file=Path("/path/to/server.py"),
            server_object="custom_app",
            name="test-server",
            with_editable=[editable_path],
        )

        assert result is True
        call_args = mock_open_deeplink.call_args[0][0]

        # Decode and verify editable path
        config_part = call_args.split("config=")[1]
        decoded = base64.urlsafe_b64decode(config_part).decode()
        config_data = json.loads(decoded)

        assert "--with-editable" in config_data["args"]
        # Check that the path was resolved (should be absolute)
        editable_idx = config_data["args"].index("--with-editable") + 1
        resolved_path = config_data["args"][editable_idx]
        assert Path(resolved_path).is_absolute()
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
            assert args_str.count("--with numpy") == 1
            assert args_str.count("--with pandas") == 1
            assert args_str.count("--with fastmcp") == 1


class TestCursorCommand:
    """Test the cursor CLI command."""

    @patch("fastmcp.cli.install.cursor.install_cursor")
    @patch("fastmcp.cli.install.cursor.process_common_args")
    async def test_cursor_command_basic(self, mock_process_args, mock_install):
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
            await cursor_command("server.py")

        mock_install.assert_called_once_with(
            file=Path("server.py"),
            server_object=None,
            name="test-server",
            with_editable=[],
            with_packages=[],
            env_vars={},
            python_version=None,
            with_requirements=None,
            project=None,
            workspace=None,
        )
        mock_exit.assert_not_called()

    @patch("fastmcp.cli.install.cursor.install_cursor")
    @patch("fastmcp.cli.install.cursor.process_common_args")
    async def test_cursor_command_failure(self, mock_process_args, mock_install):
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
            await cursor_command("server.py")

        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1
