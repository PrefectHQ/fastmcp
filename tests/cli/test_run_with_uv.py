"""Tests for the run_with_uv function and related functionality."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fastmcp.cli.run import run_with_uv


class TestRunWithUv:
    """Test the run_with_uv function."""

    @patch("subprocess.run")
    def test_run_with_uv_basic(self, mock_run):
        """Test basic run_with_uv execution."""
        mock_run.return_value = Mock(returncode=0)

        with pytest.raises(SystemExit) as exc_info:
            run_with_uv("server.py")

        assert exc_info.value.code == 0

        # Check the command that was called
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]

        expected = ["uv", "run", "--with", "fastmcp", "fastmcp", "run", "server.py"]
        assert cmd == expected

    @patch("subprocess.run")
    def test_run_with_uv_python_version(self, mock_run):
        """Test run_with_uv with Python version."""
        mock_run.return_value = Mock(returncode=0)

        with pytest.raises(SystemExit) as exc_info:
            run_with_uv("server.py", python_version="3.11")

        assert exc_info.value.code == 0

        cmd = mock_run.call_args[0][0]
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

    @patch("subprocess.run")
    def test_run_with_uv_project(self, mock_run):
        """Test run_with_uv with project directory."""
        mock_run.return_value = Mock(returncode=0)
        project_path = Path("/my/project")

        with pytest.raises(SystemExit) as exc_info:
            run_with_uv("server.py", project=project_path)

        assert exc_info.value.code == 0

        cmd = mock_run.call_args[0][0]
        # Check the basic structure
        assert cmd[:3] == ["uv", "run", "--project"]
        # Check that the project path is absolute
        assert Path(cmd[3]).is_absolute()
        # Check the rest of the command
        assert cmd[4:] == ["--with", "fastmcp", "fastmcp", "run", "server.py"]

    @patch("subprocess.run")
    def test_run_with_uv_with_packages(self, mock_run):
        """Test run_with_uv with additional packages."""
        mock_run.return_value = Mock(returncode=0)

        with pytest.raises(SystemExit) as exc_info:
            run_with_uv("server.py", with_packages=["pandas", "numpy"])

        assert exc_info.value.code == 0

        cmd = mock_run.call_args[0][0]
        expected = [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "--with",
            "numpy",  # sorted alphabetically
            "--with",
            "pandas",  # sorted alphabetically
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    @patch("subprocess.run")
    def test_run_with_uv_with_requirements(self, mock_run):
        """Test run_with_uv with requirements file."""
        mock_run.return_value = Mock(returncode=0)
        req_path = Path("requirements.txt")

        with pytest.raises(SystemExit) as exc_info:
            run_with_uv("server.py", with_requirements=req_path)

        assert exc_info.value.code == 0

        cmd = mock_run.call_args[0][0]
        expected = [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "--with-requirements",
            str(req_path.expanduser().resolve()),  # resolved to absolute path
            "fastmcp",
            "run",
            "server.py",
        ]
        assert cmd == expected

    @patch("subprocess.run")
    def test_run_with_uv_transport_options(self, mock_run):
        """Test run_with_uv with transport-related options."""
        mock_run.return_value = Mock(returncode=0)

        with pytest.raises(SystemExit) as exc_info:
            run_with_uv(
                "server.py",
                transport="http",
                host="localhost",
                port=8080,
                path="/api",
                log_level="DEBUG",
                show_banner=False,
            )

        assert exc_info.value.code == 0

        cmd = mock_run.call_args[0][0]
        expected = [
            "uv",
            "run",
            "--with",
            "fastmcp",
            "fastmcp",
            "run",
            "server.py",
            "--transport",
            "http",
            "--host",
            "localhost",
            "--port",
            "8080",
            "--path",
            "/api",
            "--log-level",
            "DEBUG",
            "--no-banner",
        ]
        assert cmd == expected

    @patch("subprocess.run")
    def test_run_with_uv_all_options(self, mock_run):
        """Test run_with_uv with all options combined."""
        mock_run.return_value = Mock(returncode=0)

        with pytest.raises(SystemExit) as exc_info:
            run_with_uv(
                "server.py",
                python_version="3.10",
                project=Path("/workspace"),
                with_packages=["pandas"],
                with_requirements=Path("reqs.txt"),
                transport="http",
                port=9000,
                show_banner=False,
            )

        assert exc_info.value.code == 0

        cmd = mock_run.call_args[0][0]

        # Check the structure piece by piece to be platform-agnostic
        assert cmd[:5] == ["uv", "run", "--python", "3.10", "--project"]
        # Check project path is absolute
        assert Path(cmd[5]).is_absolute()
        assert cmd[6:10] == ["--with", "fastmcp", "--with", "pandas"]
        assert cmd[10] == "--with-requirements"
        # Check requirements path is absolute
        assert Path(cmd[11]).is_absolute()
        assert cmd[12:] == [
            "fastmcp",
            "run",
            "server.py",
            "--transport",
            "http",
            "--port",
            "9000",
            "--no-banner",
        ]

    @patch("subprocess.run")
    def test_run_with_uv_error_handling(self, mock_run):
        """Test run_with_uv error handling."""
        mock_run.side_effect = subprocess.CalledProcessError(1, ["uv", "run"])

        with pytest.raises(SystemExit) as exc_info:
            run_with_uv("server.py")

        assert exc_info.value.code == 1

    @patch("fastmcp.cli.run.logger")
    @patch("subprocess.run")
    def test_run_with_uv_logging(self, mock_run, mock_logger):
        """Test that run_with_uv logs the command."""
        mock_run.return_value = Mock(returncode=0)

        with pytest.raises(SystemExit):
            run_with_uv("server.py", python_version="3.11")

        # Check that debug logging was called with the command
        mock_logger.debug.assert_called()
        call_args = mock_logger.debug.call_args[0][0]
        assert "Running command:" in call_args
        assert "uv run --python 3.11" in call_args
