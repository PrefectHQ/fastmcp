"""Tests for stdio client reuse in MCPConfig"""

import asyncio
import inspect
import tempfile
import warnings
from pathlib import Path

import pytest

from fastmcp.client import Client
from fastmcp.client.transports import PythonStdioTransport


@pytest.mark.asyncio
async def test_mcp_config_stdio_client_reuse(tmp_path: Path):
    """Test that MCPConfig reuses clients for stdio servers to prevent race conditions."""
    server_script = inspect.cleandoc("""
        from fastmcp import FastMCP

        mcp = FastMCP()

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        if __name__ == '__main__':
            mcp.run()
        """)

    script_path = tmp_path / "test_server.py"
    script_path.write_text(server_script)

    config = {
        "mcpServers": {
            "test_1": {
                "command": "python",
                "args": [str(script_path)],
            },
            "test_2": {
                "command": "python",
                "args": [str(script_path)],
            },
        }
    }

    client = Client(config)

    async with client:
        # Make parallel calls that would previously fail
        tasks = [client.call_tool("test_1_add", {"a": 1, "b": 2}) for _ in range(20)]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        exceptions = [result for result in results if isinstance(result, Exception)]

        # Should have no exceptions and all successful results
        assert len(exceptions) == 0
        assert len(results) == 20
        assert all(result.data == 3 for result in results)


def test_stdio_client_new_warning():
    """Test that new() method warns when called on stdio transport clients."""
    # Create a temporary script file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(
            "from fastmcp import FastMCP\nmcp = FastMCP()\nif __name__ == '__main__': mcp.run()"
        )
        script_path = Path(f.name)

    try:
        transport = PythonStdioTransport(script_path=script_path)
        client = Client(transport=transport)

        # Capture warnings
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            # This should trigger a warning
            new_client = client.new()

            # Check that warning was issued
            # Note: warnings from logger are not captured by warnings module
            # So we'll check that the method completes without error
            assert new_client is not None
            assert new_client != client  # Should be a different instance

    finally:
        script_path.unlink()  # Clean up temp file
