"""Tests for MCP configuration JSON generation."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastmcp.cli.install.mcp_config import install_mcp_config


class TestInstallMcpConfig:
    """Test install_mcp_config function."""

    def test_generates_basic_config(self):
        """Should generate basic MCP configuration with minimal options."""
        result = install_mcp_config(
            file=Path("server.py"),
            server_object=None,
            name="test-server",
        )

        assert result is True

    @patch("fastmcp.cli.install.mcp_config.print")
    def test_generates_config_with_all_options(self, mock_print):
        """Should generate MCP configuration with all options."""
        result = install_mcp_config(
            file=Path("server.py"),
            server_object="custom_server",
            name="test-server",
            with_editable=Path("/path/to/editable"),
            with_packages=["pandas", "requests"],
            env_vars={"API_KEY": "secret", "DEBUG": "true"},
        )

        assert result is True
        mock_print.assert_called_once()

        # Get the JSON output from print call
        json_output = mock_print.call_args[0][0]
        config = json.loads(json_output)

        # Verify structure (should be just the server config, not wrapped in mcpServers)
        server_config = config

        # Verify command and args
        assert server_config["command"] == "uv"
        assert "run" in server_config["args"]
        assert "--with" in server_config["args"]
        assert "fastmcp" in server_config["args"]
        assert "pandas" in server_config["args"]
        assert "requests" in server_config["args"]
        assert "--with-editable" in server_config["args"]
        assert str(Path("/path/to/editable")) in server_config["args"]

        # Verify server spec with object
        server_spec_in_args = None
        for i, arg in enumerate(server_config["args"]):
            if (
                arg == "fastmcp"
                and i + 2 < len(server_config["args"])
                and server_config["args"][i + 1] == "run"
            ):
                server_spec_in_args = server_config["args"][i + 2]
                break

        assert server_spec_in_args is not None
        assert ":custom_server" in server_spec_in_args

        # Verify environment variables
        assert server_config["env"] == {"API_KEY": "secret", "DEBUG": "true"}

    @patch("fastmcp.cli.install.mcp_config.print")
    def test_generates_config_without_env_vars(self, mock_print):
        """Should generate MCP configuration without env section when no env vars."""
        result = install_mcp_config(
            file=Path("server.py"),
            server_object=None,
            name="test-server",
        )

        assert result is True
        json_output = mock_print.call_args[0][0]
        config = json.loads(json_output)

        # Should not have env section
        assert "env" not in config

    @patch("fastmcp.cli.install.mcp_config.print")
    def test_deduplicates_packages(self, mock_print):
        """Should deduplicate packages including fastmcp."""
        result = install_mcp_config(
            file=Path("server.py"),
            server_object=None,
            name="test-server",
            with_packages=["pandas", "fastmcp", "pandas"],  # duplicates
        )

        assert result is True
        json_output = mock_print.call_args[0][0]
        config = json.loads(json_output)

        args = config["args"]

        # Count occurrences of packages
        pandas_count = sum(1 for arg in args if arg == "pandas")
        fastmcp_count = sum(1 for arg in args if arg == "fastmcp")

        # Should only appear once each for the package (fastmcp appears twice: once as package, once as command)
        assert pandas_count == 1
        assert fastmcp_count == 2  # Once in --with fastmcp, once in fastmcp run

    @patch("fastmcp.cli.install.mcp_config.print")
    def test_resolves_absolute_paths(self, mock_print):
        """Should resolve server file to absolute path."""
        result = install_mcp_config(
            file=Path("server.py"),
            server_object=None,
            name="test-server",
        )

        assert result is True
        json_output = mock_print.call_args[0][0]
        config = json.loads(json_output)

        args = config["args"]

        # Find the server spec after "fastmcp run"
        server_spec_in_args = None
        for i, arg in enumerate(args):
            if arg == "fastmcp" and i + 2 < len(args) and args[i + 1] == "run":
                server_spec_in_args = args[i + 2]
                break

        assert server_spec_in_args is not None
        assert str(Path("server.py").resolve()) in server_spec_in_args

    @patch("fastmcp.cli.install.mcp_config.print")
    def test_copy_to_clipboard_success(self, mock_print):
        """Should copy configuration to clipboard when copy=True."""
        # Mock the pyperclip module at import time
        mock_pyperclip = MagicMock()
        mock_copy = MagicMock()
        mock_pyperclip.copy = mock_copy

        with patch.dict("sys.modules", {"pyperclip": mock_pyperclip}):
            result = install_mcp_config(
                file=Path("server.py"),
                server_object=None,
                name="test-server",
                copy=True,
            )

            assert result is True
            mock_copy.assert_called_once()

            # Verify clipboard content is valid JSON
            clipboard_content = mock_copy.call_args[0][0]
            config = json.loads(clipboard_content)  # Should not raise
            assert "command" in config  # Should be server config, not wrapped

            # Should print success message
            mock_print.assert_called_once()
            assert "copied to clipboard" in str(mock_print.call_args)

    @patch("fastmcp.cli.install.mcp_config.print")
    def test_copy_to_clipboard_import_error(self, mock_print):
        """Should handle pyperclip import error gracefully."""
        with patch(
            "builtins.__import__",
            side_effect=ImportError("No module named 'pyperclip'"),
        ):
            result = install_mcp_config(
                file=Path("server.py"),
                server_object=None,
                name="test-server",
                copy=True,
            )

        assert result is False

        # Should print error message
        mock_print.assert_called_once()
        error_call = str(mock_print.call_args)
        assert "copy` flag requires pyperclip" in error_call
        assert "pip install pyperclip" in error_call

    @patch("fastmcp.cli.install.mcp_config.print")
    def test_handles_exception_gracefully(self, mock_print):
        """Should handle unexpected exceptions gracefully."""
        with patch("json.dumps", side_effect=Exception("JSON error")):
            result = install_mcp_config(
                file=Path("server.py"),
                server_object=None,
                name="test-server",
            )

        assert result is False
        mock_print.assert_called_once()
        assert "Failed to generate MCP configuration" in str(mock_print.call_args)
