"""Tests for the fastmcp project prepare command."""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.utilities.mcp_server_config import MCPServerConfig
from fastmcp.utilities.mcp_server_config.v1.environments.uv import UVEnvironment
from fastmcp.utilities.mcp_server_config.v1.sources.filesystem import FileSystemSource


class TestMCPServerConfigPrepare:
    """Test the MCPServerConfig.prepare() method."""

    @patch(
        "fastmcp.utilities.mcp_server_config.v1.mcp_server_config.MCPServerConfig.prepare_source",
        new_callable=AsyncMock,
    )
    @patch(
        "fastmcp.utilities.mcp_server_config.v1.mcp_server_config.MCPServerConfig.prepare_environment",
        new_callable=AsyncMock,
    )
    async def test_prepare_calls_both_methods(self, mock_env, mock_src):
        """Test that prepare() calls both prepare_environment and prepare_source."""
        config = MCPServerConfig(
            source=FileSystemSource(path="server.py"),
            environment=UVEnvironment(python="3.10"),
        )

        await config.prepare()

        mock_env.assert_called_once()
        mock_src.assert_called_once()

    @patch(
        "fastmcp.utilities.mcp_server_config.v1.mcp_server_config.MCPServerConfig.prepare_source",
        new_callable=AsyncMock,
    )
    @patch(
        "fastmcp.utilities.mcp_server_config.v1.mcp_server_config.MCPServerConfig.prepare_environment",
        new_callable=AsyncMock,
    )
    async def test_prepare_with_output_dir(self, mock_env, mock_src):
        """Test that prepare() with output_dir calls prepare_environment with it."""
        config = MCPServerConfig(
            source=FileSystemSource(path="server.py"),
            environment=UVEnvironment(python="3.10"),
        )

        output_path = Path("/tmp/test-env")
        await config.prepare(skip_source=False, output_dir=output_path)

        mock_env.assert_called_once_with(output_dir=output_path)
        mock_src.assert_called_once()

    @patch(
        "fastmcp.utilities.mcp_server_config.v1.mcp_server_config.MCPServerConfig.prepare_source",
        new_callable=AsyncMock,
    )
    @patch(
        "fastmcp.utilities.mcp_server_config.v1.mcp_server_config.MCPServerConfig.prepare_environment",
        new_callable=AsyncMock,
    )
    async def test_prepare_skip_source(self, mock_env, mock_src):
        """Test that prepare() skips source when skip_source=True."""
        config = MCPServerConfig(
            source=FileSystemSource(path="server.py"),
            environment=UVEnvironment(python="3.10"),
        )

        await config.prepare(skip_source=True)

        mock_env.assert_called_once_with(output_dir=None)
        mock_src.assert_not_called()

    @patch(
        "fastmcp.utilities.mcp_server_config.v1.mcp_server_config.MCPServerConfig.prepare_source",
        new_callable=AsyncMock,
    )
    @patch(
        "fastmcp.utilities.mcp_server_config.v1.environments.uv.UVEnvironment.prepare",
        new_callable=AsyncMock,
    )
    async def test_prepare_no_environment_settings(self, mock_env_prepare, mock_src):
        """Test that prepare() works with default empty environment config."""
        config = MCPServerConfig(
            source=FileSystemSource(path="server.py"),
            # environment defaults to empty Environment()
        )

        await config.prepare(skip_source=False)

        # Environment prepare should be called even with empty config
        mock_env_prepare.assert_called_once_with(output_dir=None)
        mock_src.assert_called_once()


class TestEnvironmentPrepare:
    """Test the Environment.prepare() method."""

    @patch("shutil.which")
    async def test_prepare_no_uv_installed(self, mock_which, tmp_path):
        """Test that prepare() raises error when uv is not installed."""
        mock_which.return_value = None

        env = UVEnvironment(python="3.10")

        with pytest.raises(RuntimeError, match="uv is not installed"):
            await env.prepare(tmp_path / "test-env")

    @patch("subprocess.run")
    @patch("shutil.which")
    async def test_prepare_no_settings(self, mock_which, mock_run, tmp_path):
        """Test that prepare() does nothing when no settings are configured."""
        mock_which.return_value = "/usr/bin/uv"

        env = UVEnvironment()  # No settings

        await env.prepare(tmp_path / "test-env")

        # Should not run any commands
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch("shutil.which")
    async def test_prepare_with_python(self, mock_which, mock_run, tmp_path):
        """Test that prepare() runs uv with python version."""
        mock_which.return_value = "/usr/bin/uv"
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Environment cached", stderr=""
        )

        env = UVEnvironment(python="3.10")

        await env.prepare(tmp_path / "test-env")

        # Should run multiple uv commands for initializing the project
        assert mock_run.call_count > 0

        # Check the first call should be uv init
        first_call_args = mock_run.call_args_list[0][0][0]
        assert first_call_args[0] == "uv"
        assert "init" in first_call_args

    @patch("subprocess.run")
    @patch("shutil.which")
    async def test_prepare_with_dependencies(self, mock_which, mock_run, tmp_path):
        """Test that prepare() includes dependencies."""
        mock_which.return_value = "/usr/bin/uv"
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        env = UVEnvironment(dependencies=["numpy", "pandas"])

        await env.prepare(tmp_path / "test-env")

        # Should run multiple uv commands, one of which should be uv add
        assert mock_run.call_count > 0

        # Find the add command call
        add_call = None
        for call_args, _ in mock_run.call_args_list:
            args = call_args[0]
            if "add" in args:
                add_call = args
                break

        assert add_call is not None, "Should have called uv add"
        assert "numpy" in add_call
        assert "pandas" in add_call
        assert "fastmcp" in add_call  # Always added

    @patch("subprocess.run")
    @patch("shutil.which")
    async def test_prepare_command_fails(self, mock_which, mock_run, tmp_path):
        """Test that prepare() raises error when uv command fails."""
        mock_which.return_value = "/usr/bin/uv"
        mock_run.side_effect = subprocess.CalledProcessError(
            1, ["uv"], stderr="Package not found"
        )

        env = UVEnvironment(python="3.10")

        with pytest.raises(RuntimeError, match="Failed to initialize project"):
            await env.prepare(tmp_path / "test-env")


class TestProjectPrepareCommand:
    """Test the CLI project prepare command."""

    @patch("fastmcp.utilities.mcp_server_config.MCPServerConfig.from_file")
    @patch("fastmcp.utilities.mcp_server_config.MCPServerConfig.find_config")
    async def test_project_prepare_auto_detect(self, mock_find, mock_from_file):
        """Test project prepare with auto-detected config."""
        from fastmcp.cli.cli import prepare

        # Setup mocks
        mock_find.return_value = Path("fastmcp.json")
        mock_config = AsyncMock()
        mock_from_file.return_value = mock_config

        # Run command with output_dir
        with patch("sys.exit"):
            with patch("fastmcp.cli.cli.console.print") as mock_print:
                await prepare(config_path=None, output_dir="./test-env")

        # Should find and load config
        mock_find.assert_called_once()
        mock_from_file.assert_called_once_with(Path("fastmcp.json"))

        # Should call prepare with output_dir
        mock_config.prepare.assert_called_once_with(
            skip_source=False,
            output_dir=Path("./test-env"),
        )

        # Should print success message
        mock_print.assert_called()
        success_call = mock_print.call_args_list[-1][0][0]
        assert "Project prepared successfully" in success_call

    @patch("pathlib.Path.exists")
    @patch("fastmcp.utilities.mcp_server_config.MCPServerConfig.from_file")
    async def test_project_prepare_explicit_path(self, mock_from_file, mock_exists):
        """Test project prepare with explicit config path."""
        from fastmcp.cli.cli import prepare

        # Setup mocks
        mock_exists.return_value = True
        mock_config = AsyncMock()
        mock_from_file.return_value = mock_config

        # Run command with explicit path
        with patch("fastmcp.cli.cli.console.print"):
            await prepare(config_path="myconfig.json", output_dir="./test-env")

        # Should load specified config
        mock_from_file.assert_called_once_with(Path("myconfig.json"))

        # Should call prepare
        mock_config.prepare.assert_called_once_with(
            skip_source=False,
            output_dir=Path("./test-env"),
        )

    @patch("fastmcp.utilities.mcp_server_config.MCPServerConfig.find_config")
    async def test_project_prepare_no_config_found(self, mock_find):
        """Test project prepare when no config is found."""
        from fastmcp.cli.cli import prepare

        # Setup mocks
        mock_find.return_value = None

        # Run command without output_dir - should exit with error for missing output_dir
        with pytest.raises(SystemExit) as exc_info:
            with patch("fastmcp.cli.cli.logger.error") as mock_error:
                await prepare(config_path=None, output_dir=None)

        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1
        mock_error.assert_called()
        error_msg = mock_error.call_args[0][0]
        assert "--output-dir parameter is required" in error_msg

    @patch("pathlib.Path.exists")
    async def test_project_prepare_config_not_exists(self, mock_exists):
        """Test project prepare when specified config doesn't exist."""
        from fastmcp.cli.cli import prepare

        # Setup mocks
        mock_exists.return_value = False

        # Run command without output_dir - should exit with error for missing output_dir
        with pytest.raises(SystemExit) as exc_info:
            with patch("fastmcp.cli.cli.logger.error") as mock_error:
                await prepare(config_path="missing.json", output_dir=None)

        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1
        mock_error.assert_called()
        error_msg = mock_error.call_args[0][0]
        assert "--output-dir parameter is required" in error_msg

    @patch("pathlib.Path.exists")
    @patch("fastmcp.utilities.mcp_server_config.MCPServerConfig.from_file")
    async def test_project_prepare_failure(self, mock_from_file, mock_exists):
        """Test project prepare when prepare() fails."""
        from fastmcp.cli.cli import prepare

        # Setup mocks
        mock_exists.return_value = True
        mock_config = AsyncMock()
        mock_config.prepare.side_effect = RuntimeError("Preparation failed")
        mock_from_file.return_value = mock_config

        # Run command - should exit with error
        with pytest.raises(SystemExit) as exc_info:
            with patch("fastmcp.cli.cli.console.print") as mock_print:
                await prepare(config_path="config.json", output_dir="./test-env")

        assert isinstance(exc_info.value, SystemExit)
        assert exc_info.value.code == 1
        # Should print error message
        error_call = mock_print.call_args_list[-1][0][0]
        assert "Failed to prepare project" in error_call
