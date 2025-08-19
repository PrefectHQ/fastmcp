"""Tests for FastMCP configuration file support with nested structure."""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from fastmcp.utilities.fastmcp_config import (
    DeploymentConfig,
    EntrypointConfig,
    EnvironmentConfig,
    FastMCPConfig,
)


class TestEntrypointConfig:
    """Test EntrypointConfig class."""

    def test_string_entrypoint(self):
        """Test that string entrypoint is converted to EntrypointConfig."""
        config = FastMCPConfig(entrypoint="server.py")
        # With the new validator, this should be converted to EntrypointConfig
        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "server.py"
        assert config.entrypoint.object is None

        # get_entrypoint should return the same object
        entrypoint = config.get_entrypoint()
        assert isinstance(entrypoint, EntrypointConfig)
        assert entrypoint.file == "server.py"
        assert entrypoint.object is None

    def test_string_entrypoint_with_object(self):
        """Test string entrypoint with :object syntax."""
        config = FastMCPConfig(entrypoint="server.py:app")
        # With the new validator, this should be converted to EntrypointConfig
        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "server.py"
        assert config.entrypoint.object == "app"

        # get_entrypoint should return the same object
        entrypoint = config.get_entrypoint()
        assert isinstance(entrypoint, EntrypointConfig)
        assert entrypoint.file == "server.py"
        assert entrypoint.object == "app"

    def test_object_entrypoint(self):
        """Test EntrypointConfig object format."""
        config = FastMCPConfig(
            entrypoint=EntrypointConfig(file="src/server.py", object="mcp")
        )
        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "src/server.py"
        assert config.entrypoint.object == "mcp"

    def test_get_entrypoint_path_resolution(self, tmp_path):
        """Test that get_entrypoint resolves paths relative to config file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        server_dir = tmp_path / "src"
        server_dir.mkdir()
        server_file = server_dir / "server.py"
        server_file.write_text("# server")

        config = FastMCPConfig(entrypoint="../src/server.py")
        entrypoint = config.get_entrypoint(config_dir / "fastmcp.json")

        # Should resolve to absolute path
        assert Path(entrypoint.file).is_absolute()
        assert Path(entrypoint.file) == server_file.resolve()


class TestEnvironmentConfig:
    """Test EnvironmentConfig class."""

    def test_environment_config_fields(self):
        """Test all EnvironmentConfig fields."""
        config = FastMCPConfig(
            entrypoint="server.py",
            environment={
                "python": "3.12",
                "dependencies": ["requests", "numpy>=2.0"],
                "requirements": "requirements.txt",
                "project": ".",
                "editable": "../my-package",
            },
        )

        env = config.environment
        assert env.python == "3.12"
        assert env.dependencies == ["requests", "numpy>=2.0"]
        assert env.requirements == "requirements.txt"
        assert env.project == "."
        assert env.editable == "../my-package"

    def test_needs_uv(self):
        """Test needs_uv() method."""
        # No environment config - doesn't need UV
        config = FastMCPConfig(entrypoint="server.py")
        assert not config.environment.needs_uv()

        # Empty environment - doesn't need UV
        config = FastMCPConfig(entrypoint="server.py", environment={})
        assert not config.environment.needs_uv()

        # With dependencies - needs UV
        config = FastMCPConfig(
            entrypoint="server.py", environment={"dependencies": ["requests"]}
        )
        assert config.environment.needs_uv()

        # With Python version - needs UV
        config = FastMCPConfig(entrypoint="server.py", environment={"python": "3.12"})
        assert config.environment.needs_uv()

    def test_build_uv_args(self):
        """Test build_uv_args() method."""
        config = FastMCPConfig(
            entrypoint="server.py",
            environment={
                "python": "3.12",
                "dependencies": ["requests", "numpy"],
                "requirements": "requirements.txt",
                "project": ".",
            },
        )

        args = config.environment.build_uv_args(["fastmcp", "run", "server.py"])

        assert args[0] == "run"
        assert "--python" in args
        assert "3.12" in args
        assert "--project" in args
        assert "--with" in args
        assert "fastmcp" in args
        assert "requests" in args
        assert "numpy" in args
        assert "--with-requirements" in args
        assert "requirements.txt" in args
        assert "fastmcp" in args[-3:]
        assert "run" in args[-2:]
        assert "server.py" in args[-1:]

    def test_merge_with_cli_args(self):
        """Test merge_with_cli_args() method."""
        config = FastMCPConfig(
            entrypoint="server.py",
            environment={
                "python": "3.11",
                "dependencies": ["requests"],
            },
        )

        # CLI args should take precedence
        merged = config.environment.merge_with_cli_args(
            python="3.12",  # Override
            with_packages=["numpy"],  # Add to dependencies
            with_requirements=None,
            project=None,
        )

        assert merged["python"] == "3.12"  # CLI override
        assert set(merged["with_packages"]) == {"requests", "numpy"}  # Merged
        assert merged["with_requirements"] is None
        assert merged["project"] is None

    def test_run_with_uv(self):
        """Test run_with_uv() subprocess execution."""
        config = FastMCPConfig(
            entrypoint="server.py", environment={"dependencies": ["requests"]}
        )

        # run_with_uv calls sys.exit, so we expect SystemExit
        with pytest.raises(SystemExit) as exc_info:
            # This will fail because we're running exit(1)
            # but it tests that the subprocess is called correctly
            config.environment.run_with_uv(["python", "-c", "exit(1)"])

        # Check that it exited with code 1
        assert exc_info.value.code == 1


class TestDeploymentConfig:
    """Test DeploymentConfig class."""

    def test_deployment_config_fields(self):
        """Test all DeploymentConfig fields."""
        config = FastMCPConfig(
            entrypoint="server.py",
            deployment={
                "transport": "http",
                "host": "0.0.0.0",
                "port": 8000,
                "path": "/api/",
                "log_level": "DEBUG",
                "env": {"API_KEY": "secret"},
                "cwd": "./work",
                "args": ["--debug"],
            },
        )

        deploy = config.deployment
        assert deploy.transport == "http"
        assert deploy.host == "0.0.0.0"
        assert deploy.port == 8000
        assert deploy.path == "/api/"
        assert deploy.log_level == "DEBUG"
        assert deploy.env == {"API_KEY": "secret"}
        assert deploy.cwd == "./work"
        assert deploy.args == ["--debug"]

    def test_merge_with_cli_args(self):
        """Test DeploymentConfig merge_with_cli_args() method."""
        config = FastMCPConfig(
            entrypoint="server.py",
            deployment={
                "transport": "stdio",
                "port": 3000,
                "log_level": "INFO",
            },
        )

        # CLI args should take precedence
        merged = config.deployment.merge_with_cli_args(
            transport="http",  # Override
            host="localhost",  # New value
            port=None,  # Keep config value
            path=None,
            log_level="DEBUG",  # Override
            server_args=["--test"],
        )

        assert merged["transport"] == "http"  # CLI override
        assert merged["host"] == "localhost"  # CLI value
        assert merged["port"] == 3000  # Config value (CLI was None)
        assert merged["log_level"] == "DEBUG"  # CLI override
        assert merged["server_args"] == ["--test"]  # CLI value

    def test_apply_runtime_settings(self, tmp_path):
        """Test apply_runtime_settings() method."""
        import os

        # Create config with env vars and cwd
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        config = FastMCPConfig(
            entrypoint="server.py",
            deployment={
                "env": {"TEST_VAR": "test_value"},
                "cwd": "work",
            },
        )

        original_cwd = os.getcwd()
        original_env = os.environ.get("TEST_VAR")

        try:
            config.deployment.apply_runtime_settings(tmp_path / "fastmcp.json")

            # Check environment variable was set
            assert os.environ["TEST_VAR"] == "test_value"

            # Check working directory was changed
            assert Path.cwd() == work_dir.resolve()

        finally:
            # Restore original state
            os.chdir(original_cwd)
            if original_env is None:
                os.environ.pop("TEST_VAR", None)
            else:
                os.environ["TEST_VAR"] = original_env

    def test_env_var_interpolation(self, tmp_path):
        """Test environment variable interpolation in deployment env."""
        import os

        # Set up test environment variables
        os.environ["BASE_URL"] = "example.com"
        os.environ["ENV_NAME"] = "production"

        config = FastMCPConfig(
            entrypoint="server.py",
            deployment={
                "env": {
                    "API_URL": "https://api.${BASE_URL}/v1",
                    "DATABASE": "postgres://${ENV_NAME}.db",
                    "PREFIXED": "MY_${ENV_NAME}_SERVER",
                    "MISSING": "value_${NONEXISTENT}_here",
                    "STATIC": "no_interpolation",
                }
            },
        )

        original_values = {
            key: os.environ.get(key)
            for key in ["API_URL", "DATABASE", "PREFIXED", "MISSING", "STATIC"]
        }

        try:
            config.deployment.apply_runtime_settings()

            # Check interpolated values
            assert os.environ["API_URL"] == "https://api.example.com/v1"
            assert os.environ["DATABASE"] == "postgres://production.db"
            assert os.environ["PREFIXED"] == "MY_production_SERVER"
            # Missing variables should keep the placeholder
            assert os.environ["MISSING"] == "value_${NONEXISTENT}_here"
            # Static values should remain unchanged
            assert os.environ["STATIC"] == "no_interpolation"

        finally:
            # Clean up
            os.environ.pop("BASE_URL", None)
            os.environ.pop("ENV_NAME", None)
            for key, value in original_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class TestFastMCPConfig:
    """Test FastMCPConfig root configuration."""

    def test_minimal_config(self):
        """Test creating a config with only required fields."""
        config = FastMCPConfig(entrypoint="server.py")
        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "server.py"
        assert config.entrypoint.object is None
        # Environment and deployment are now always present but empty
        assert isinstance(config.environment, EnvironmentConfig)
        assert isinstance(config.deployment, DeploymentConfig)
        # Check they have no values set
        assert not config.environment.needs_uv()
        assert all(
            getattr(config.deployment, field, None) is None
            for field in DeploymentConfig.model_fields
        )

    def test_nested_structure(self):
        """Test the nested configuration structure."""
        config = FastMCPConfig(
            entrypoint="server.py",
            environment={
                "python": "3.12",
                "dependencies": ["fastmcp"],
            },
            deployment={
                "transport": "stdio",
                "log_level": "INFO",
            },
        )

        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "server.py"
        assert config.entrypoint.object is None
        assert isinstance(config.environment, EnvironmentConfig)
        assert isinstance(config.deployment, DeploymentConfig)

    def test_from_file(self, tmp_path):
        """Test loading config from JSON file with nested structure."""
        config_data = {
            "$schema": "https://gofastmcp.com/schemas/fastmcp_config/v1.json",
            "entrypoint": {"file": "src/server.py", "object": "app"},
            "environment": {"python": "3.12", "dependencies": ["requests"]},
            "deployment": {"transport": "http", "port": 8000},
        }

        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps(config_data))

        config = FastMCPConfig.from_file(config_file)

        # When loaded from JSON with object format, it becomes EntrypointConfig
        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "src/server.py"
        assert config.entrypoint.object == "app"
        assert config.environment.python == "3.12"
        assert config.environment.dependencies == ["requests"]
        assert config.deployment.transport == "http"
        assert config.deployment.port == 8000

    def test_from_file_with_string_entrypoint(self, tmp_path):
        """Test loading config with string entrypoint."""
        config_data = {
            "entrypoint": "server.py:mcp",
            "environment": {"dependencies": ["fastmcp"]},
        }

        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps(config_data))

        config = FastMCPConfig.from_file(config_file)
        # String entrypoint with : should be converted to EntrypointConfig
        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "server.py"
        assert config.entrypoint.object == "mcp"

        # get_entrypoint should return the same
        entrypoint = config.get_entrypoint()
        assert entrypoint.file == "server.py"
        assert entrypoint.object == "mcp"

    def test_string_entrypoint_with_object_and_environment(self, tmp_path):
        """Test that file.py:object syntax works with environment config."""
        config_data = {
            "entrypoint": "src/server.py:app",
            "environment": {"python": "3.12", "dependencies": ["fastmcp", "requests"]},
            "deployment": {"transport": "http", "port": 8000},
        }

        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps(config_data))

        config = FastMCPConfig.from_file(config_file)

        # Should be parsed into EntrypointConfig
        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "src/server.py"
        assert config.entrypoint.object == "app"

        # Environment config should still work
        assert config.environment.python == "3.12"
        assert config.environment.dependencies == ["fastmcp", "requests"]

        # Deployment config should still work
        assert config.deployment.transport == "http"
        assert config.deployment.port == 8000

    def test_find_config_in_current_dir(self, tmp_path):
        """Test finding config in current directory."""
        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps({"entrypoint": "server.py"}))

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            found = FastMCPConfig.find_config()
            assert found == config_file
        finally:
            os.chdir(original_cwd)

    def test_find_config_not_in_parent_dir(self, tmp_path):
        """Test that config is NOT found in parent directory."""
        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps({"entrypoint": "server.py"}))

        subdir = tmp_path / "subdir"
        subdir.mkdir()

        # Should NOT find config in parent directory
        found = FastMCPConfig.find_config(subdir)
        assert found is None

    def test_find_config_in_specified_dir(self, tmp_path):
        """Test finding config in the specified directory."""
        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps({"entrypoint": "server.py"}))

        # Should find config when looking in the directory that contains it
        found = FastMCPConfig.find_config(tmp_path)
        assert found == config_file

    def test_find_config_not_found(self, tmp_path):
        """Test when config is not found."""
        found = FastMCPConfig.find_config(tmp_path)
        assert found is None

    def test_invalid_transport(self, tmp_path):
        """Test loading config with invalid transport value."""
        config_data = {
            "entrypoint": "server.py",
            "deployment": {"transport": "invalid_transport"},
        }

        config_file = tmp_path / "fastmcp.json"
        config_file.write_text(json.dumps(config_data))

        with pytest.raises(ValidationError):
            FastMCPConfig.from_file(config_file)

    def test_optional_sections(self):
        """Test that all config sections are optional except entrypoint."""
        # Only entrypoint is required
        config = FastMCPConfig(entrypoint="server.py")
        assert isinstance(config.entrypoint, EntrypointConfig)
        assert config.entrypoint.file == "server.py"
        # Environment and deployment are now always present but may be empty
        assert isinstance(config.environment, EnvironmentConfig)
        assert isinstance(config.deployment, DeploymentConfig)

        # Only environment with values
        config = FastMCPConfig(entrypoint="server.py", environment={"python": "3.12"})
        assert config.environment.python == "3.12"
        assert isinstance(config.deployment, DeploymentConfig)
        assert all(
            getattr(config.deployment, field, None) is None
            for field in DeploymentConfig.model_fields
        )

        # Only deployment with values
        config = FastMCPConfig(entrypoint="server.py", deployment={"transport": "http"})
        assert isinstance(config.environment, EnvironmentConfig)
        assert all(
            getattr(config.environment, field, None) is None
            for field in EnvironmentConfig.model_fields
        )
        assert config.deployment.transport == "http"
