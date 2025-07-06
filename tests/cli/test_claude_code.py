"""Tests for Claude Code CLI integration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastmcp.cli.install.claude_code import (
    check_claude_code_available,
    find_claude_command,
    install_claude_code,
)


class TestFindClaudeCommand:
    """Test find_claude_command function."""

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_finds_command_in_default_location(self, mock_exists, mock_run):
        """Should find claude in default installation location."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(stdout="1.0.43 (Claude Code)")

        result = find_claude_command()

        expected_path = str(Path.home() / ".claude" / "local" / "claude")
        assert result == expected_path
        mock_run.assert_called_once_with(
            [expected_path, "--version"], check=True, capture_output=True, text=True
        )

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_rejects_non_claude_code_binary(self, mock_exists, mock_run):
        """Should reject binary that isn't Claude Code."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(stdout="Some other claude 1.0.0")

        result = find_claude_command()

        assert result is None

    @patch("subprocess.run")
    @patch("pathlib.Path.exists")
    def test_handles_subprocess_error(self, mock_exists, mock_run):
        """Should handle subprocess errors gracefully."""
        from subprocess import CalledProcessError

        mock_exists.return_value = True
        mock_run.side_effect = CalledProcessError(1, "claude")

        result = find_claude_command()

        assert result is None

    @patch("pathlib.Path.exists")
    def test_no_command_found(self, mock_exists):
        """Should return None when binary doesn't exist."""
        mock_exists.return_value = False

        result = find_claude_command()

        assert result is None


class TestCheckClaudeCodeAvailable:
    """Test check_claude_code_available function."""

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    def test_available_when_command_found(self, mock_find):
        """Should return True when claude command is found."""
        mock_find.return_value = "/usr/local/bin/claude"

        result = check_claude_code_available()

        assert result is True

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    def test_not_available_when_command_not_found(self, mock_find):
        """Should return False when claude command is not found."""
        mock_find.return_value = None

        result = check_claude_code_available()

        assert result is False


class TestInstallClaudeCode:
    """Test install_claude_code function."""

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    @patch("fastmcp.cli.install.claude_code.print")
    def test_fails_when_claude_not_found(self, mock_print, mock_find):
        """Should return False and print error when Claude Code CLI not found."""
        mock_find.return_value = None

        result = install_claude_code(Path("server.py"), None, "test-server")

        assert result is False
        mock_print.assert_called_once()
        assert "Claude Code CLI not found" in str(mock_print.call_args)

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    @patch("subprocess.run")
    def test_successful_installation(self, mock_run, mock_find):
        """Should successfully install when command succeeds."""
        mock_find.return_value = "/usr/local/bin/claude"
        mock_run.return_value = MagicMock()

        result = install_claude_code(Path("server.py"), None, "test-server")

        assert result is True
        mock_run.assert_called_once()

        # Check the command that was run
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "/usr/local/bin/claude"
        assert "mcp" in call_args
        assert "add" in call_args
        assert "test-server" in call_args
        assert "--" in call_args
        assert "uv" in call_args

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    @patch("subprocess.run")
    @patch("fastmcp.cli.install.claude_code.print")
    def test_handles_subprocess_error(self, mock_print, mock_run, mock_find):
        """Should handle subprocess errors and return False."""
        from subprocess import CalledProcessError

        mock_find.return_value = "/usr/local/bin/claude"
        mock_run.side_effect = CalledProcessError(
            1, "claude", stderr="Permission denied"
        )

        result = install_claude_code(Path("server.py"), None, "test-server")

        assert result is False
        mock_print.assert_called_once()
        assert "Failed to install" in str(mock_print.call_args)
        assert "Permission denied" in str(mock_print.call_args)

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    @patch("subprocess.run")
    def test_builds_correct_command_with_options(self, mock_run, mock_find):
        """Should build correct command with all options."""
        mock_find.return_value = "/usr/local/bin/claude"
        mock_run.return_value = MagicMock()

        install_claude_code(
            file=Path("server.py"),
            server_object="custom_server",
            name="test-server",
            with_editable=Path("/path/to/editable"),
            with_packages=["pandas", "requests"],
            env_vars={"API_KEY": "secret", "DEBUG": "true"},
        )

        # Check the command that was run
        call_args = mock_run.call_args[0][0]

        # Should have claude command
        assert call_args[0] == "/usr/local/bin/claude"
        assert "mcp" in call_args
        assert "add" in call_args

        # Should have environment variables
        assert "-e" in call_args
        env_vars = []
        for i, arg in enumerate(call_args):
            if arg == "-e" and i + 1 < len(call_args):
                env_vars.append(call_args[i + 1])
        assert "API_KEY=secret" in env_vars
        assert "DEBUG=true" in env_vars

        # Should have server name
        assert "test-server" in call_args

        # Should have separator
        assert "--" in call_args

        # Should have uv command with packages
        assert "uv" in call_args
        assert "run" in call_args
        assert "--with" in call_args
        assert "fastmcp" in call_args
        assert "pandas" in call_args
        assert "requests" in call_args
        assert "--with-editable" in call_args
        assert str(Path("/path/to/editable")) in call_args

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    @patch("subprocess.run")
    def test_resolves_absolute_paths(self, mock_run, mock_find):
        """Should resolve server spec to absolute path."""
        mock_find.return_value = "/usr/local/bin/claude"
        mock_run.return_value = MagicMock()

        install_claude_code(Path("server.py"), None, "test-server")

        call_args = mock_run.call_args[0][0]

        # Find the server spec after "fastmcp run"
        server_spec_in_args = None
        for i, arg in enumerate(call_args):
            if (
                arg == "fastmcp"
                and i + 2 < len(call_args)
                and call_args[i + 1] == "run"
            ):
                server_spec_in_args = call_args[i + 2]
                break

        assert server_spec_in_args is not None
        assert str(Path("server.py").resolve()) in server_spec_in_args

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    @patch("subprocess.run")
    def test_handles_server_spec_with_object(self, mock_run, mock_find):
        """Should correctly handle server spec with object notation."""
        mock_find.return_value = "/usr/local/bin/claude"
        mock_run.return_value = MagicMock()

        install_claude_code(Path("server.py"), "custom_object", "test-server")

        call_args = mock_run.call_args[0][0]

        # Find the server spec after "fastmcp run"
        server_spec_in_args = None
        for i, arg in enumerate(call_args):
            if (
                arg == "fastmcp"
                and i + 2 < len(call_args)
                and call_args[i + 1] == "run"
            ):
                server_spec_in_args = call_args[i + 2]
                break

        assert server_spec_in_args is not None
        assert ":custom_object" in server_spec_in_args
        assert str(Path("server.py").resolve()) in server_spec_in_args

    @patch("fastmcp.cli.install.claude_code.find_claude_command")
    @patch("subprocess.run")
    def test_deduplicates_packages(self, mock_run, mock_find):
        """Should deduplicate packages in the command."""
        mock_find.return_value = "/usr/local/bin/claude"
        mock_run.return_value = MagicMock()

        install_claude_code(
            file=Path("server.py"),
            server_object=None,
            name="test-server",
            with_packages=["pandas", "fastmcp", "pandas"],  # duplicates
        )

        call_args = mock_run.call_args[0][0]

        # Count occurrences of pandas
        pandas_count = sum(1 for arg in call_args if arg == "pandas")
        fastmcp_count = sum(1 for arg in call_args if arg == "fastmcp")

        # Should only appear once each for the package (fastmcp appears twice: once as package, once as command)
        assert pandas_count == 1
        assert fastmcp_count == 2  # Once in --with fastmcp, once in fastmcp run
