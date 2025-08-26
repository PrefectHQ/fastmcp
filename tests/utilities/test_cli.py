"""Tests for CLI utility functions."""

from fastmcp.utilities.fastmcp_config.v1.fastmcp_config import Environment


class TestEnvironmentBuildUVArgs:
    """Test the Environment.build_uv_args() method."""

    def test_build_uv_args_basic(self):
        """Test building basic uv args with no environment config."""
        env = Environment()
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = ["run", "fastmcp", "run", "server.py"]
        assert args == expected

    def test_build_uv_args_with_editable(self):
        """Test building uv args with editable package."""
        editable_path = "/path/to/package"
        env = Environment(editable=[editable_path])
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--with-editable",
            editable_path,
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    def test_build_uv_args_with_packages(self):
        """Test building uv args with additional packages."""
        env = Environment(dependencies=["pkg1", "pkg2"])
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--with",
            "pkg1",
            "--with",
            "pkg2",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    def test_build_uv_args_with_python_version(self):
        """Test building uv args with Python version."""
        env = Environment(python="3.10")
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--python",
            "3.10",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    def test_build_uv_args_with_requirements(self):
        """Test building uv args with requirements file."""
        requirements_path = "/path/to/requirements.txt"
        env = Environment(requirements=requirements_path)
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--with-requirements",
            requirements_path,
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    def test_build_uv_args_with_project(self):
        """Test building uv args with project directory."""
        project_path = "/path/to/project"
        env = Environment(project=project_path)
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = ["run", "--project", project_path, "fastmcp", "run", "server.py"]
        assert args == expected

    def test_build_uv_args_with_everything(self):
        """Test building uv args with all options."""
        requirements_path = "/path/to/requirements.txt"
        editable_path = "/local/pkg"
        env = Environment(
            python="3.10",
            dependencies=["pandas", "numpy"],
            requirements=requirements_path,
            editable=[editable_path],
        )
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
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
        assert args == expected

    def test_build_uv_args_no_command(self):
        """Test building uv args without command."""
        env = Environment(dependencies=["pkg1"])
        args = env.build_uv_args()
        expected = ["run", "--with", "pkg1"]
        assert args == expected

    def test_build_uv_args_with_string_command(self):
        """Test building uv args with string command."""
        env = Environment()
        args = env.build_uv_args("python")
        expected = ["run", "python"]
        assert args == expected

    def test_build_uv_args_project_with_extras(self):
        """Test that project flag works with additional dependencies."""
        project_path = "/path/to/project"
        env = Environment(
            project=project_path,
            python="3.10",  # Should be ignored with project
            dependencies=["pandas"],  # Should be added on top of project
            editable=["/pkg"],  # Should be added on top of project
        )
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
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
        assert args == expected


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
