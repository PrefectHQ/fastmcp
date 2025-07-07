"""Tests for the run module functionality."""

import sys
from unittest.mock import Mock, patch

import pytest

from fastmcp.cli.run import (
    create_client_server,
    import_server,
    import_server_with_args,
    is_url,
    parse_file_path,
    run_command,
)


class TestUrlDetection:
    """Test URL detection functionality."""

    def test_is_url_valid_http(self):
        """Test detection of valid HTTP URLs."""
        assert is_url("http://example.com")
        assert is_url("http://localhost:8080")
        assert is_url("http://127.0.0.1:3000/path")

    def test_is_url_valid_https(self):
        """Test detection of valid HTTPS URLs."""
        assert is_url("https://example.com")
        assert is_url("https://api.example.com/mcp")
        assert is_url("https://localhost:8443")

    def test_is_url_invalid(self):
        """Test detection of non-URLs."""
        assert not is_url("server.py")
        assert not is_url("/path/to/server.py")
        assert not is_url("server.py:app")
        assert not is_url("ftp://example.com")  # Not http/https
        assert not is_url("file:///path/to/file")


class TestFilePathParsing:
    """Test file path parsing functionality."""

    def test_parse_file_path_simple(self, tmp_path):
        """Test parsing simple file path without object."""
        test_file = tmp_path / "server.py"
        test_file.write_text("# test server")

        file_path, server_object = parse_file_path(str(test_file))
        assert file_path == test_file.resolve()
        assert server_object is None

    def test_parse_file_path_with_object(self, tmp_path):
        """Test parsing file path with object specification."""
        test_file = tmp_path / "server.py"
        test_file.write_text("# test server")

        file_path, server_object = parse_file_path(f"{test_file}:app")
        assert file_path == test_file.resolve()
        assert server_object == "app"

    def test_parse_file_path_complex_object(self, tmp_path):
        """Test parsing file path with complex object specification."""
        test_file = tmp_path / "server.py"
        test_file.write_text("# test server")

        # The current implementation splits on the last colon, so file:module:app
        # becomes file_path="file:module" and server_object="app"
        # We need to create a file with a colon in the name for this test
        complex_file = tmp_path / "server:module.py"
        complex_file.write_text("# test server")

        file_path, server_object = parse_file_path(f"{complex_file}:app")
        assert file_path == complex_file.resolve()
        assert server_object == "app"

    def test_parse_file_path_nonexistent(self):
        """Test parsing nonexistent file path exits."""
        with pytest.raises(SystemExit) as exc_info:
            parse_file_path("nonexistent.py")
        assert exc_info.value.code == 1

    def test_parse_file_path_directory(self, tmp_path):
        """Test parsing directory path exits."""
        with pytest.raises(SystemExit) as exc_info:
            parse_file_path(str(tmp_path))
        assert exc_info.value.code == 1

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific test")
    def test_parse_file_path_windows_drive(self, tmp_path):
        """Test parsing Windows path with drive letter."""
        # This test would only work on Windows with actual drive letters
        # For now, just test the logic doesn't break with colons
        test_file = tmp_path / "server.py"
        test_file.write_text("# test server")

        # Should handle paths that might look like Windows drives
        file_path, server_object = parse_file_path(str(test_file))
        assert file_path == test_file.resolve()
        assert server_object is None


class TestServerImport:
    """Test server import functionality."""

    def test_import_server_with_standard_name(self, tmp_path):
        """Test importing server with standard object name."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp
mcp = fastmcp.FastMCP("TestServer")
""")

        with patch("fastmcp.cli.run.sys.path") as mock_path:
            mock_path.__contains__ = Mock(return_value=False)
            mock_path.insert = Mock()

            # Mock the actual import process
            with patch(
                "fastmcp.cli.run.importlib.util.spec_from_file_location"
            ) as mock_spec_from_file:
                with patch(
                    "fastmcp.cli.run.importlib.util.module_from_spec"
                ) as mock_module_from_spec:
                    # Setup mock module
                    mock_module = Mock()
                    mock_module.mcp = Mock()
                    mock_module_from_spec.return_value = mock_module

                    # Setup mock spec
                    mock_spec = Mock()
                    mock_spec.loader = Mock()
                    mock_spec_from_file.return_value = mock_spec

                    server = import_server(test_file)
                    assert server == mock_module.mcp

    def test_import_server_with_custom_object(self, tmp_path):
        """Test importing server with custom object name."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp
my_app = fastmcp.FastMCP("TestServer")
""")

        with patch("fastmcp.cli.run.sys.path") as mock_path:
            mock_path.__contains__ = Mock(return_value=False)
            mock_path.insert = Mock()

            with patch(
                "fastmcp.cli.run.importlib.util.spec_from_file_location"
            ) as mock_spec_from_file:
                with patch(
                    "fastmcp.cli.run.importlib.util.module_from_spec"
                ) as mock_module_from_spec:
                    mock_module = Mock()
                    mock_module.my_app = Mock()
                    mock_module_from_spec.return_value = mock_module

                    mock_spec = Mock()
                    mock_spec.loader = Mock()
                    mock_spec_from_file.return_value = mock_spec

                    server = import_server(test_file, "my_app")
                    assert server == mock_module.my_app

    def test_import_server_no_standard_names(self, tmp_path):
        """Test importing server when no standard names exist."""
        test_file = tmp_path / "server.py"
        test_file.write_text("# No server objects")

        with patch("fastmcp.cli.run.sys.path"):
            with patch(
                "fastmcp.cli.run.importlib.util.spec_from_file_location"
            ) as mock_spec_from_file:
                with patch(
                    "fastmcp.cli.run.importlib.util.module_from_spec"
                ) as mock_module_from_spec:
                    mock_module = Mock()

                    # Mock hasattr behavior for standard names
                    def mock_hasattr(obj, name):
                        return name not in ["mcp", "server", "app"]

                    with patch("builtins.hasattr", side_effect=mock_hasattr):
                        mock_module_from_spec.return_value = mock_module

                        mock_spec = Mock()
                        mock_spec.loader = Mock()
                        mock_spec_from_file.return_value = mock_spec

                        with pytest.raises(SystemExit) as exc_info:
                            import_server(test_file)
                        assert exc_info.value.code == 1

    def test_import_server_nonexistent_object(self, tmp_path):
        """Test importing nonexistent server object."""
        test_file = tmp_path / "server.py"
        test_file.write_text("# No server objects")

        with patch("fastmcp.cli.run.sys.path"):
            with patch(
                "fastmcp.cli.run.importlib.util.spec_from_file_location"
            ) as mock_spec_from_file:
                with patch(
                    "fastmcp.cli.run.importlib.util.module_from_spec"
                ) as mock_module_from_spec:
                    mock_module = Mock()
                    mock_module.nonexistent = None
                    mock_module_from_spec.return_value = mock_module

                    mock_spec = Mock()
                    mock_spec.loader = Mock()
                    mock_spec_from_file.return_value = mock_spec

                    with pytest.raises(SystemExit) as exc_info:
                        import_server(test_file, "nonexistent")
                    assert exc_info.value.code == 1


class TestServerImportWithArgs:
    """Test server import with command line arguments."""

    @patch("fastmcp.cli.run.import_server")
    def test_import_server_with_args(self, mock_import_server, tmp_path):
        """Test importing server with command line arguments."""
        test_file = tmp_path / "server.py"
        mock_server = Mock()
        mock_import_server.return_value = mock_server

        original_argv = sys.argv[:]
        try:
            result = import_server_with_args(
                test_file, "app", ["--config", "test.json", "--debug"]
            )

            assert result == mock_server
            mock_import_server.assert_called_once_with(test_file, "app")

        finally:
            sys.argv = original_argv

    @patch("fastmcp.cli.run.import_server")
    def test_import_server_no_args(self, mock_import_server, tmp_path):
        """Test importing server without command line arguments."""
        test_file = tmp_path / "server.py"
        mock_server = Mock()
        mock_import_server.return_value = mock_server

        result = import_server_with_args(test_file, "app")

        assert result == mock_server
        mock_import_server.assert_called_once_with(test_file, "app")


class TestClientServer:
    """Test client server creation."""

    def test_create_client_server(self):
        """Test creating server from client URL."""
        # Patch the import at the builtins level since it's a local import
        with patch("builtins.__import__") as mock_import:
            mock_fastmcp = Mock()
            mock_import.return_value = mock_fastmcp

            mock_client = Mock()
            mock_server = Mock()
            mock_fastmcp.Client.return_value = mock_client
            mock_fastmcp.FastMCP.from_client.return_value = mock_server

            result = create_client_server("http://example.com")

            assert result == mock_server
            mock_fastmcp.Client.assert_called_once_with("http://example.com")
            mock_fastmcp.FastMCP.from_client.assert_called_once_with(mock_client)

    def test_create_client_server_failure(self):
        """Test client server creation failure."""
        with patch("builtins.__import__") as mock_import:
            mock_fastmcp = Mock()
            mock_import.return_value = mock_fastmcp
            mock_fastmcp.Client.side_effect = Exception("Connection failed")

            with pytest.raises(SystemExit) as exc_info:
                create_client_server("http://example.com")
            assert exc_info.value.code == 1


class TestRunCommand:
    """Test the main run command functionality."""

    @patch("fastmcp.cli.run.create_client_server")
    def test_run_command_url(self, mock_create_client_server):
        """Test running command with URL."""
        mock_server = Mock()
        mock_create_client_server.return_value = mock_server

        run_command("http://example.com")

        mock_create_client_server.assert_called_once_with("http://example.com")
        mock_server.run.assert_called_once()

    @patch("fastmcp.cli.run.import_server_with_args")
    @patch("fastmcp.cli.run.parse_file_path")
    def test_run_command_file(self, mock_parse_file_path, mock_import_server):
        """Test running command with file path."""
        mock_file = Mock()
        mock_parse_file_path.return_value = (mock_file, "app")
        mock_server = Mock()
        mock_server.name = "TestServer"
        mock_import_server.return_value = mock_server

        run_command("server.py:app")

        mock_parse_file_path.assert_called_once_with("server.py:app")
        mock_import_server.assert_called_once_with(mock_file, "app", None)
        mock_server.run.assert_called_once()

    @patch("fastmcp.cli.run.import_server_with_args")
    @patch("fastmcp.cli.run.parse_file_path")
    def test_run_command_with_options(self, mock_parse_file_path, mock_import_server):
        """Test running command with various options."""
        mock_file = Mock()
        mock_parse_file_path.return_value = (mock_file, None)
        mock_server = Mock()
        mock_server.name = "TestServer"
        mock_import_server.return_value = mock_server

        run_command(
            "server.py",
            transport="http",
            host="localhost",
            port=8080,
            log_level="DEBUG",
            server_args=["--config", "test.json"],
            show_banner=False,
        )

        mock_server.run.assert_called_once_with(
            transport="http",
            host="localhost",
            port=8080,
            log_level="DEBUG",
            show_banner=False,
        )

    @patch("fastmcp.cli.run.import_server_with_args")
    @patch("fastmcp.cli.run.parse_file_path")
    def test_run_command_server_failure(self, mock_parse_file_path, mock_import_server):
        """Test run command when server run fails."""
        mock_file = Mock()
        mock_parse_file_path.return_value = (mock_file, None)
        mock_server = Mock()
        mock_server.name = "TestServer"
        mock_server.run.side_effect = Exception("Server failed")
        mock_import_server.return_value = mock_server

        with pytest.raises(SystemExit) as exc_info:
            run_command("server.py")
        assert exc_info.value.code == 1
