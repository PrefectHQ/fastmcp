"""Tests for CLI utility functions."""

from fastmcp.utilities.mcp_server_config.v1.mcp_server_config import Environment


class TestEnvironmentBuildUVRunCommand:
    """Test the Environment.build_uv_run_command() method."""

    def test_build_uv_run_command_basic(self):
        """Test building basic uv command with no environment config."""
        env = Environment()
        cmd = env.build_uv_run_command(["fastmcp", "run", "server.py"])
        expected = ["uv", "run", "fastmcp", "run", "server.py"]
        assert cmd == expected

    def test_build_uv_run_command_with_editable(self):
        """Test building uv command with editable package."""
        editable_path = "/path/to/package"
        env = Environment(editable=[editable_path])
        cmd = env.build_uv_run_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--with-editable",
            editable_path,
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_run_command_with_packages(self):
        """Test building uv command with additional packages."""
        env = Environment(dependencies=["pkg1", "pkg2"])
        cmd = env.build_uv_run_command(["fastmcp", "run", "server.py"])
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
        env = Environment(python="3.10")
        cmd = env.build_uv_run_command(["fastmcp", "run", "server.py"])
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
        requirements_path = "/path/to/requirements.txt"
        env = Environment(requirements=requirements_path)
        cmd = env.build_uv_run_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--with-requirements",
            requirements_path,
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_run_command_with_project(self):
        """Test building uv command with project directory."""
        project_path = "/path/to/project"
        env = Environment(project=project_path)
        cmd = env.build_uv_run_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--project",
            project_path,
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_run_command_with_everything(self):
        """Test building uv command with all options."""
        requirements_path = "/path/to/requirements.txt"
        editable_path = "/local/pkg"
        env = Environment(
            python="3.10",
            dependencies=["pandas", "numpy"],
            requirements=requirements_path,
            editable=[editable_path],
        )
        cmd = env.build_uv_run_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--python",
            "3.10",
            "--with",
            "pandas",
            "--with",
            "numpy",
            "--with-requirements",
            requirements_path,
            "--with-editable",
            editable_path,
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    # Note: These tests are removed because build_uv_run_command now requires a command
    # and only accepts a list, not optional or string commands

    def test_build_uv_run_command_project_with_extras(self):
        """Test that project flag works with additional dependencies."""
        project_path = "/path/to/project"
        env = Environment(
            project=project_path,
            python="3.10",  # Should be ignored with project
            dependencies=["pandas"],  # Should be added on top of project
            editable=["/pkg"],  # Should be added on top of project
        )
        cmd = env.build_uv_run_command(["fastmcp", "run", "server.py"])
        expected = [
            "uv",
            "run",
            "--project",
            project_path,
            "--with",
            "pandas",
            "--with-editable",
            "/pkg",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected


class TestEnvironmentNeedsUV:
    """Test the Environment.needs_uv() method."""

    def test_needs_uv_with_python(self):
        """Test that needs_uv returns True with Python version."""
        env = Environment(python="3.10")
        assert env.needs_uv() is True

    def test_needs_uv_with_dependencies(self):
        """Test that needs_uv returns True with dependencies."""
        env = Environment(dependencies=["pandas"])
        assert env.needs_uv() is True

    def test_needs_uv_with_requirements(self):
        """Test that needs_uv returns True with requirements."""
        env = Environment(requirements="/path/to/requirements.txt")
        assert env.needs_uv() is True

    def test_needs_uv_with_project(self):
        """Test that needs_uv returns True with project."""
        env = Environment(project="/path/to/project")
        assert env.needs_uv() is True

    def test_needs_uv_with_editable(self):
        """Test that needs_uv returns True with editable."""
        env = Environment(editable=["/pkg"])
        assert env.needs_uv() is True

    def test_needs_uv_empty(self):
        """Test that needs_uv returns False with empty config."""
        env = Environment()
        assert env.needs_uv() is False

    def test_needs_uv_with_empty_lists(self):
        """Test that needs_uv returns False with empty lists."""
        env = Environment(dependencies=None, editable=None)
        assert env.needs_uv() is False
