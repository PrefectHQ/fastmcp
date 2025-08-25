from pathlib import Path
from unittest.mock import patch

from fastmcp.utilities.fastmcp_config.v1.fastmcp_config import Environment


class TestEnvironmentBuildUVArgs:
    """Test the Environment.build_uv_args() method."""

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_basic(self, mock_dev_path):
        """Test building basic uv args."""
        env = Environment()
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = ["run", "--with", "fastmcp", "fastmcp", "run", "server.py"]
        assert args == expected

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_with_editable(self, mock_dev_path):
        """Test building uv args with editable package."""
        editable_path = "/path/to/package"
        env = Environment(editable=editable_path)
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--with",
            "fastmcp",
            "--with-editable",
            editable_path,
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_with_packages(self, mock_dev_path):
        """Test building uv args with additional packages."""
        env = Environment(dependencies=["pkg1", "pkg2"])
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
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
        assert args == expected

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_with_python_version(self, mock_dev_path):
        """Test building uv args with Python version."""
        env = Environment(python="3.11")
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--python",
            "3.11",
            "--with",
            "fastmcp",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_with_project(self, mock_dev_path):
        """Test building uv args with project directory."""
        project_path = "/path/to/project"
        env = Environment(project=project_path)
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--project",
            project_path,
            "--with",
            "fastmcp",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_with_requirements(self, mock_dev_path):
        """Test building uv args with requirements file."""
        req_path = "requirements.txt"
        env = Environment(requirements=req_path)
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--with",
            "fastmcp",
            "--with-requirements",
            req_path,
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_with_all_options(self, mock_dev_path):
        """Test building uv args with all options."""
        project_path = "/my/project"
        editable_path = "/local/pkg"
        requirements_path = "reqs.txt"
        env = Environment(
            python="3.10",
            project=project_path,
            dependencies=["pandas", "numpy"],
            requirements=requirements_path,
            editable=editable_path,
        )
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--python",
            "3.10",
            "--project",
            project_path,
            "--with",
            "fastmcp",
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

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_no_command(self, mock_dev_path):
        """Test building uv args with no command."""
        env = Environment(python="3.11")
        args = env.build_uv_args()
        expected = ["run", "--python", "3.11", "--with", "fastmcp"]
        assert args == expected

    @patch.object(Environment, "_find_fastmcp_dev_path", return_value=None)
    def test_build_uv_args_string_command(self, mock_dev_path):
        """Test building uv args with string command."""
        env = Environment()
        args = env.build_uv_args("python")
        expected = ["run", "--with", "fastmcp", "python"]
        assert args == expected

    def test_needs_uv_true(self):
        """Test that needs_uv returns True when environment settings are present."""
        env = Environment(python="3.11")
        assert env.needs_uv() is True

        env = Environment(dependencies=["pkg"])
        assert env.needs_uv() is True

        env = Environment(requirements="reqs.txt")
        assert env.needs_uv() is True

        env = Environment(project="/project")
        assert env.needs_uv() is True

        env = Environment(editable="/pkg")
        assert env.needs_uv() is True

    def test_needs_uv_false(self):
        """Test that needs_uv returns False when no environment settings are present."""
        env = Environment()
        assert env.needs_uv() is False

    @patch.object(Environment, "_find_fastmcp_dev_path")
    def test_build_uv_args_development_mode(self, mock_dev_path):
        """Test building uv args in development mode (when fastmcp project is found)."""
        # Mock finding the development path
        dev_path = Path("/path/to/fastmcp/dev")
        mock_dev_path.return_value = dev_path

        env = Environment()
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = [
            "run",
            "--with-editable",
            str(dev_path),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert args == expected

    @patch.object(Environment, "_find_fastmcp_dev_path")
    def test_build_uv_args_production_mode(self, mock_dev_path):
        """Test building uv args in production mode (when no fastmcp project is found)."""
        # Mock not finding the development path
        mock_dev_path.return_value = None

        env = Environment()
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = ["run", "--with", "fastmcp", "fastmcp", "run", "server.py"]
        assert args == expected

    @patch("pathlib.Path.cwd")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_find_fastmcp_dev_path_found(self, mock_read_text, mock_exists, mock_cwd):
        """Test finding fastmcp development path when pyproject.toml exists."""
        # Set up mock current directory
        mock_cwd_path = Path("/path/to/fastmcp")
        mock_cwd.return_value = mock_cwd_path

        # Mock pyproject.toml exists and contains fastmcp name
        mock_exists.return_value = True
        mock_read_text.return_value = """[project]
name = "fastmcp"
version = "2.0.0"
"""

        env = Environment()
        result = env._find_fastmcp_dev_path()

        assert result == mock_cwd_path

    @patch("pathlib.Path.cwd")
    @patch("pathlib.Path.exists")
    def test_find_fastmcp_dev_path_not_found(self, mock_exists, mock_cwd):
        """Test not finding fastmcp development path when no pyproject.toml exists."""
        # Set up mock current directory
        mock_cwd_path = Path("/some/other/directory")
        mock_cwd.return_value = mock_cwd_path

        # Mock pyproject.toml doesn't exist
        mock_exists.return_value = False

        env = Environment()
        result = env._find_fastmcp_dev_path()

        assert result is None

    @patch("pathlib.Path.cwd")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_find_fastmcp_dev_path_wrong_project(
        self, mock_read_text, mock_exists, mock_cwd
    ):
        """Test not finding fastmcp when pyproject.toml exists but is for different project."""
        # Set up mock current directory
        mock_cwd_path = Path("/path/to/other/project")
        mock_cwd.return_value = mock_cwd_path

        # Mock pyproject.toml exists but is for different project
        mock_exists.return_value = True
        mock_read_text.return_value = """[project]
name = "other-project"
version = "1.0.0"
"""

        env = Environment()
        result = env._find_fastmcp_dev_path()

        assert result is None
