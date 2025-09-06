from pathlib import Path

from fastmcp.cli.install import install_app


class TestInstallApp:
    """Test the install subapp."""

    def test_install_app_exists(self):
        """Test that the install app is properly configured."""
        # install_app.name is a tuple in cyclopts
        assert "install" in install_app.name
        assert "Install MCP servers" in install_app.help

    def test_install_commands_registered(self):
        """Test that all install commands are registered."""
        # Check that the app has the expected help text and structure
        # This is a simpler check that doesn't rely on internal methods
        assert hasattr(install_app, "help")
        assert "Install MCP servers" in install_app.help

        # We can test that the commands parse without errors
        try:
            install_app.parse_args(["claude-code", "--help"])
            install_app.parse_args(["claude-desktop", "--help"])
            install_app.parse_args(["cursor", "--help"])
            install_app.parse_args(["gemini-cli", "--help"])
            install_app.parse_args(["mcp-json", "--help"])
        except SystemExit:
            # Help commands exit with 0, that's expected
            pass


class TestClaudeCodeInstall:
    """Test claude-code install command."""

    def test_claude_code_basic(self):
        """Test basic claude-code install command parsing."""
        # Parse command with correct parameter names
        command, bound, _ = install_app.parse_args(
            ["claude-code", "server.py", "--name", "test-server"]
        )

        # Verify parsing was successful
        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        assert bound.arguments["server_name"] == "test-server"

    def test_claude_code_with_options(self):
        """Test claude-code install with various options."""
        command, bound, _ = install_app.parse_args(
            [
                "claude-code",
                "server.py",
                "--name",
                "test-server",
                "--with",
                "package1",
                "--with",
                "package2",
                "--env",
                "VAR1=value1",
            ]
        )

        assert bound.arguments["with_packages"] == ["package1", "package2"]
        assert bound.arguments["env_vars"] == ["VAR1=value1"]

    def test_claude_code_with_new_options(self):
        """Test claude-code install with new uv options."""
        from pathlib import Path

        command, bound, _ = install_app.parse_args(
            [
                "claude-code",
                "server.py",
                "--python",
                "3.11",
                "--project",
                "/workspace",
                "--with-requirements",
                "requirements.txt",
            ]
        )

        assert bound.arguments["python"] == "3.11"
        assert bound.arguments["project"] == Path("/workspace")
        assert bound.arguments["with_requirements"] == Path("requirements.txt")


class TestClaudeDesktopInstall:
    """Test claude-desktop install command."""

    def test_claude_desktop_basic(self):
        """Test basic claude-desktop install command parsing."""
        command, bound, _ = install_app.parse_args(
            ["claude-desktop", "server.py", "--name", "test-server"]
        )

        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        assert bound.arguments["server_name"] == "test-server"

    def test_claude_desktop_with_env_vars(self):
        """Test claude-desktop install with environment variables."""
        command, bound, _ = install_app.parse_args(
            [
                "claude-desktop",
                "server.py",
                "--name",
                "test-server",
                "--env",
                "VAR1=value1",
                "--env",
                "VAR2=value2",
            ]
        )

        assert bound.arguments["env_vars"] == ["VAR1=value1", "VAR2=value2"]

    def test_claude_desktop_with_new_options(self):
        """Test claude-desktop install with new uv options."""
        from pathlib import Path

        command, bound, _ = install_app.parse_args(
            [
                "claude-desktop",
                "server.py",
                "--python",
                "3.10",
                "--project",
                "/my/project",
                "--with-requirements",
                "reqs.txt",
            ]
        )

        assert bound.arguments["python"] == "3.10"
        assert bound.arguments["project"] == Path("/my/project")
        assert bound.arguments["with_requirements"] == Path("reqs.txt")


class TestCursorInstall:
    """Test cursor install command."""

    def test_cursor_basic(self):
        """Test basic cursor install command parsing."""
        command, bound, _ = install_app.parse_args(
            ["cursor", "server.py", "--name", "test-server"]
        )

        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        assert bound.arguments["server_name"] == "test-server"

    def test_cursor_with_options(self):
        """Test cursor install with options."""
        command, bound, _ = install_app.parse_args(
            ["cursor", "server.py", "--name", "test-server"]
        )

        assert bound.arguments["server_spec"] == "server.py"
        assert bound.arguments["server_name"] == "test-server"


class TestMcpJsonInstall:
    """Test mcp-json install command."""

    def test_mcp_json_basic(self):
        """Test basic mcp-json install command parsing."""
        command, bound, _ = install_app.parse_args(
            ["mcp-json", "server.py", "--name", "test-server"]
        )

        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        assert bound.arguments["server_name"] == "test-server"

    def test_mcp_json_with_copy(self):
        """Test mcp-json install with copy to clipboard option."""
        command, bound, _ = install_app.parse_args(
            ["mcp-json", "server.py", "--name", "test-server", "--copy"]
        )

        assert bound.arguments["copy"] is True


class TestGeminiCliInstall:
    """Test gemini-cli install command."""

    def test_gemini_cli_basic(self):
        """Test basic gemini-cli install command parsing."""
        # Parse command with correct parameter names
        command, bound, _ = install_app.parse_args(
            ["gemini-cli", "server.py", "--name", "test-server"]
        )

        # Verify parsing was successful
        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"
        assert bound.arguments["server_name"] == "test-server"

    def test_gemini_cli_with_options(self):
        """Test gemini-cli install with various options."""
        command, bound, _ = install_app.parse_args(
            [
                "gemini-cli",
                "server.py",
                "--name",
                "test-server",
                "--with",
                "package1",
                "--with",
                "package2",
                "--env",
                "VAR1=value1",
            ]
        )

        assert bound.arguments["with_packages"] == ["package1", "package2"]
        assert bound.arguments["env_vars"] == ["VAR1=value1"]

    def test_gemini_cli_with_new_options(self):
        """Test gemini-cli install with new uv options."""
        from pathlib import Path

        command, bound, _ = install_app.parse_args(
            [
                "gemini-cli",
                "server.py",
                "--python",
                "3.11",
                "--project",
                "/workspace",
                "--with-requirements",
                "requirements.txt",
            ]
        )

        assert bound.arguments["python"] == "3.11"
        assert bound.arguments["project"] == Path("/workspace")
        assert bound.arguments["with_requirements"] == Path("requirements.txt")


class TestInstallCommandParsing:
    """Test command parsing and error handling."""

    def test_install_minimal_args(self):
        """Test install commands with minimal required arguments."""
        # Each command should work with just a server spec
        commands_to_test = [
            ["claude-code", "server.py"],
            ["claude-desktop", "server.py"],
            ["cursor", "server.py"],
            ["gemini-cli", "server.py"],
        ]

        for cmd_args in commands_to_test:
            command, bound, _ = install_app.parse_args(cmd_args)
            assert command is not None
            assert bound.arguments["server_spec"] == "server.py"

    def test_mcp_json_minimal(self):
        """Test that mcp-json works with minimal arguments."""
        # Should work with just server spec
        command, bound, _ = install_app.parse_args(["mcp-json", "server.py"])
        assert command is not None
        assert bound.arguments["server_spec"] == "server.py"

    def test_python_option(self):
        """Test --python option for all install commands."""
        commands_to_test = [
            ["claude-code", "server.py", "--python", "3.11"],
            ["claude-desktop", "server.py", "--python", "3.11"],
            ["cursor", "server.py", "--python", "3.11"],
            ["gemini-cli", "server.py", "--python", "3.11"],
            ["mcp-json", "server.py", "--python", "3.11"],
        ]

        for cmd_args in commands_to_test:
            command, bound, _ = install_app.parse_args(cmd_args)
            assert command is not None
            assert bound.arguments["python"] == "3.11"

    def test_with_requirements_option(self):
        """Test --with-requirements option for all install commands."""
        commands_to_test = [
            ["claude-code", "server.py", "--with-requirements", "requirements.txt"],
            ["claude-desktop", "server.py", "--with-requirements", "requirements.txt"],
            ["cursor", "server.py", "--with-requirements", "requirements.txt"],
            ["gemini-cli", "server.py", "--with-requirements", "requirements.txt"],
            ["mcp-json", "server.py", "--with-requirements", "requirements.txt"],
        ]

        for cmd_args in commands_to_test:
            command, bound, _ = install_app.parse_args(cmd_args)
            assert command is not None
            assert str(bound.arguments["with_requirements"]) == "requirements.txt"

    def test_project_option(self):
        """Test --project option for all install commands."""
        commands_to_test = [
            ["claude-code", "server.py", "--project", "/path/to/project"],
            ["claude-desktop", "server.py", "--project", "/path/to/project"],
            ["cursor", "server.py", "--project", "/path/to/project"],
            ["gemini-cli", "server.py", "--project", "/path/to/project"],
            ["mcp-json", "server.py", "--project", "/path/to/project"],
        ]

        for cmd_args in commands_to_test:
            command, bound, _ = install_app.parse_args(cmd_args)
            assert command is not None
            assert str(bound.arguments["project"]) == str(Path("/path/to/project"))
