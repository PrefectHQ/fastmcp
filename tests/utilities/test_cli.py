from pathlib import Path

from fastmcp.utilities.cli import build_uv_command


class TestBuildUVCommand:
    """Test the build_uv_command function."""

    def test_build_uv_command_basic(self):
        """Test building basic uv command."""
        cmd = build_uv_command("server.py")
        expected = ["uv", "run", "--with", "fastmcp", "fastmcp", "run", "server.py"]
        assert cmd == expected

    def test_build_uv_command_with_editable(self):
        """Test building uv command with editable package."""
        editable_path = Path("/path/to/package")
        cmd = build_uv_command("server.py", with_editable=editable_path)
        expected = [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "--with-editable",
            str(editable_path.expanduser().resolve()),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_command_with_packages(self):
        """Test building uv command with additional packages."""
        cmd = build_uv_command("server.py", with_packages=["pkg1", "pkg2"])
        expected = [
            "uv",
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
        assert cmd == expected

    def test_build_uv_command_with_python_version(self):
        """Test building uv command with Python version."""
        cmd = build_uv_command("server.py", python_version="3.11")
        expected = [
            "uv",
            "run",
            "--python",
            "3.11",
            "--with",
            "fastmcp",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_command_with_project(self):
        """Test building uv command with project directory."""
        project_path = Path("/path/to/project")
        cmd = build_uv_command("server.py", project=project_path)
        expected = [
            "uv",
            "run",
            "--project",
            str(project_path.expanduser().resolve()),
            "--with",
            "fastmcp",
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_command_with_requirements(self):
        """Test building uv command with requirements file."""
        req_path = Path("requirements.txt")
        cmd = build_uv_command("server.py", with_requirements=req_path)
        expected = [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "--with-requirements",
            str(req_path.expanduser().resolve()),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_build_uv_command_with_all_options(self):
        """Test building uv command with all options."""
        project_path = Path("/my/project")
        editable_path = Path("/local/pkg")
        requirements_path = Path("reqs.txt")
        cmd = build_uv_command(
            "server.py",
            python_version="3.10",
            project=project_path,
            with_packages=["pandas", "numpy"],
            with_requirements=requirements_path,
            with_editable=editable_path,
        )
        expected = [
            "uv",
            "run",
            "--python",
            "3.10",
            "--project",
            str(project_path.expanduser().resolve()),
            "--with",
            "fastmcp",
            "--with",
            "numpy",
            "--with",
            "pandas",
            "--with-editable",
            str(editable_path.expanduser().resolve()),
            "--with-requirements",
            str(requirements_path.expanduser().resolve()),
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    def test_with_editable_resolves_dot(self):
        """Test that '.' in with_editable becomes absolute."""
        cmd = build_uv_command("server.py", with_editable=Path("."))
        idx = cmd.index("--with-editable") + 1
        assert Path(cmd[idx]).is_absolute()

    def test_with_editable_resolves_tilde(self):
        """Test that '~' in with_editable is expanded."""
        cmd = build_uv_command("server.py", with_editable=Path("~/project"))
        idx = cmd.index("--with-editable") + 1
        assert Path(cmd[idx]).is_absolute()
        assert "~" not in cmd[idx]

    def test_with_requirements_resolves_dot(self):
        """Test that '.' in with_requirements becomes absolute."""
        cmd = build_uv_command("server.py", with_requirements=Path("./reqs.txt"))
        idx = cmd.index("--with-requirements") + 1
        assert Path(cmd[idx]).is_absolute()

    def test_with_requirements_resolves_tilde(self):
        """Test that '~' in with_requirements is expanded."""
        cmd = build_uv_command("server.py", with_requirements=Path("~/reqs.txt"))
        idx = cmd.index("--with-requirements") + 1
        assert Path(cmd[idx]).is_absolute()
        assert "~" not in cmd[idx]

    def test_project_resolves_relative(self):
        """Test that relative path in project becomes absolute."""
        cmd = build_uv_command("server.py", project=Path("../project"))
        idx = cmd.index("--project") + 1
        assert Path(cmd[idx]).is_absolute()

    def test_project_resolves_tilde(self):
        """Test that '~' in project is expanded."""
        cmd = build_uv_command("server.py", project=Path("~/work"))
        idx = cmd.index("--project") + 1
        assert Path(cmd[idx]).is_absolute()
        assert "~" not in cmd[idx]
