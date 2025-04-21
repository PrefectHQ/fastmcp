import pytest


@pytest.fixture
def server_file(tmp_path):
    """Create a simple server file for testing"""
    server_path = tmp_path / "test_server.py"
    server_path.write_text(
        """
from fastmcp import FastMCP

mcp = FastMCP(name="TestServer")

@mcp.tool()
def hello(name: str) -> str:
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()
"""
    )
    return server_path
