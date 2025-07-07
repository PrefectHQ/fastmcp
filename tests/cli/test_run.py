import pytest

from fastmcp.cli.run import (
    import_server,
    is_url,
    parse_file_path,
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


class TestServerImport:
    """Test server import functionality using real files."""

    async def test_import_server_basic_mcp(self, tmp_path):
        """Test importing server with basic FastMCP server."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

mcp = fastmcp.FastMCP("TestServer")

@mcp.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"
""")

        server = import_server(test_file)
        assert server.name == "TestServer"
        tools = await server.get_tools()
        assert "greet" in tools

    async def test_import_server_with_main_block(self, tmp_path):
        """Test importing server with if __name__ == '__main__' block."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

app = fastmcp.FastMCP("MainServer")

@app.tool
def calculate(x: int, y: int) -> int:
    return x + y

if __name__ == "__main__":
    app.run()
""")

        server = import_server(test_file)
        assert server.name == "MainServer"
        tools = await server.get_tools()
        assert "calculate" in tools

    def test_import_server_standard_names(self, tmp_path):
        """Test automatic detection of standard names (mcp, server, app)."""
        # Test with 'mcp' name
        mcp_file = tmp_path / "mcp_server.py"
        mcp_file.write_text("""
import fastmcp
mcp = fastmcp.FastMCP("MCPServer")
""")

        server = import_server(mcp_file)
        assert server.name == "MCPServer"

        # Test with 'server' name
        server_file = tmp_path / "server_server.py"
        server_file.write_text("""
import fastmcp
server = fastmcp.FastMCP("ServerServer")
""")

        server = import_server(server_file)
        assert server.name == "ServerServer"

        # Test with 'app' name
        app_file = tmp_path / "app_server.py"
        app_file.write_text("""
import fastmcp
app = fastmcp.FastMCP("AppServer")
""")

        server = import_server(app_file)
        assert server.name == "AppServer"

    async def test_import_server_nonstandard_name(self, tmp_path):
        """Test importing server with non-standard object name."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

my_custom_server = fastmcp.FastMCP("CustomServer")

@my_custom_server.tool
def custom_tool() -> str:
    return "custom"
""")

        server = import_server(test_file, "my_custom_server")
        assert server.name == "CustomServer"
        tools = await server.get_tools()
        assert "custom_tool" in tools

    def test_import_server_no_standard_names_fails(self, tmp_path):
        """Test importing server when no standard names exist fails."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

other_name = fastmcp.FastMCP("OtherServer")
""")

        with pytest.raises(SystemExit) as exc_info:
            import_server(test_file)
        assert exc_info.value.code == 1

    def test_import_server_nonexistent_object_fails(self, tmp_path):
        """Test importing nonexistent server object fails."""
        test_file = tmp_path / "server.py"
        test_file.write_text("""
import fastmcp

mcp = fastmcp.FastMCP("TestServer")
""")

        with pytest.raises(SystemExit) as exc_info:
            import_server(test_file, "nonexistent")
        assert exc_info.value.code == 1
