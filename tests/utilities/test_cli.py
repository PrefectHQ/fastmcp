"""Tests for CLI utility functions."""

from pathlib import Path

from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment


class TestEnvironmentBuildUVRunCommand:
    """Test the Environment.build_uv_run_command() method."""

    def test_build_uv_run_command_basic(self):
        """Test building basic uv command with no environment config."""
        env = UVEnvironment()
        cmd = env.build_command(["fastmcp", "run", "server.py"])
        # With no config, the command should be returned unchanged
        expected = ["fastmcp", "run", "server.py"]
        assert cmd == expected

    def test_build_uv_run_command_with_editable(self):
        """Test building uv command with editable package."""
        editable_path = Path("/path/to/package")
        env = UVEnvironment(editable=[editable_path])
        cmd = env.build_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--with-editable",
            str(editable_path.resolve()),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_run_command_with_packages(self):
        """Test building uv command with additional packages."""
        env = UVEnvironment(dependencies=["pkg1", "pkg2"])
        cmd = env.build_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--with",
            "pkg1",
            "--with",
            "pkg2",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_run_command_with_python_version(self):
        """Test building uv command with Python version."""
        env = UVEnvironment(python="3.10")
        cmd = env.build_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--python",
            "3.10",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_run_command_with_requirements(self):
        """Test building uv command with requirements file."""
        requirements_path = Path("/path/to/requirements.txt")
        env = UVEnvironment(requirements=requirements_path)
        cmd = env.build_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--with-requirements",
            str(requirements_path.resolve()),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_run_command_with_project(self):
        """Test building uv command with project directory."""
        project_path = Path("/path/to/project")
        env = UVEnvironment(project=project_path)
        cmd = env.build_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--project",
            str(project_path.resolve()),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_run_command_with_everything(self):
        """Test building uv command with all options."""
        requirements_path = Path("/path/to/requirements.txt")
        editable_path = Path("/local/pkg")
        env = UVEnvironment(
            python="3.10",
            dependencies=["pandas", "numpy"],
            requirements=requirements_path,
            editable=[editable_path],
        )
        cmd = env.build_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--python",
            "3.10",
            "--with",
            "numpy",
            "--with",
            "pandas",
            "--with-requirements",
            str(requirements_path.resolve()),
            "--with-editable",
            str(editable_path.resolve()),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    # Note: These tests are removed because build_uv_run_command now requires a command
    # and only accepts a list, not optional or string commands

    def test_build_uv_run_command_project_with_extras(self):
        """Test that project flag works with additional dependencies."""
        project_path = Path("/path/to/project")
        editable_path = Path("/pkg")
        env = UVEnvironment(
            project=project_path,
            python="3.10",  # Should be ignored with project
            dependencies=["pandas"],  # Should be added on top of project
            editable=[editable_path],  # Should be added on top of project
        )
        cmd = env.build_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--project",
            str(project_path.resolve()),
            "--with",
            "pandas",
            "--with-editable",
            str(editable_path.resolve()),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected


class TestEnvironmentNeedsUV:
    """Test the Environment.needs_uv() method."""

    def test_needs_uv_with_python(self):
        """Test that needs_uv returns True with Python version."""
        env = UVEnvironment(python="3.10")
        assert env._must_run_with_uv() is True

    def test_needs_uv_with_dependencies(self):
        """Test that needs_uv returns True with dependencies."""
        env = UVEnvironment(dependencies=["pandas"])
        assert env._must_run_with_uv() is True

    def test_needs_uv_with_requirements(self):
        """Test that needs_uv returns True with requirements."""
        env = UVEnvironment(requirements=Path("/path/to/requirements.txt"))
        assert env._must_run_with_uv() is True

    def test_needs_uv_with_project(self):
        """Test that needs_uv returns True with project."""
        env = UVEnvironment(project=Path("/path/to/project"))
        assert env._must_run_with_uv() is True

    def test_needs_uv_with_editable(self):
        """Test that needs_uv returns True with editable."""
        env = UVEnvironment(editable=[Path("/pkg")])
        assert env._must_run_with_uv() is True

    def test_needs_uv_empty(self):
        """Test that needs_uv returns False with empty config."""
        env = UVEnvironment()
        assert env._must_run_with_uv() is False

    def test_needs_uv_with_empty_lists(self):
        """Test that needs_uv returns False with empty lists."""
        env = UVEnvironment(dependencies=None, editable=None)
        assert env._must_run_with_uv() is False
