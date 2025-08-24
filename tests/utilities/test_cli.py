from fastmcp.utilities.fastmcp_config.v1.fastmcp_config import Environment


class TestEnvironmentBuildUVArgs:
    """Test the Environment.build_uv_args() method."""

    def test_build_uv_args_basic(self):
        """Test building basic uv args."""
        env = Environment()
        args = env.build_uv_args(["fastmcp", "run", "server.py"])
        expected = ["run", "--with", "fastmcp", "fastmcp", "run", "server.py"]
        assert args == expected

    def test_build_uv_args_with_editable(self):
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

    def test_build_uv_args_with_packages(self):
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

    def test_build_uv_args_with_python_version(self):
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

    def test_build_uv_args_with_project(self):
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

    def test_build_uv_args_with_requirements(self):
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

    def test_build_uv_args_with_all_options(self):
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

    def test_build_uv_args_no_command(self):
        """Test building uv args with no command."""
        env = Environment(python="3.11")
        args = env.build_uv_args()
        expected = ["run", "--python", "3.11", "--with", "fastmcp"]
        assert args == expected

    def test_build_uv_args_string_command(self):
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
