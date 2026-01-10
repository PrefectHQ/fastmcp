"""Filesystem-based MCP server using FileSystemProvider.

This example demonstrates how to use FileSystemProvider to automatically
discover and register tools, resources, and prompts from the filesystem.

Run:
    fastmcp run examples/filesystem-provider/server.py

Inspect:
    fastmcp inspect examples/filesystem-provider/server.py

Dev mode (re-scan files on every request):
    Change reload=True below, then modify files while the server runs.
"""

from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers import FileSystemProvider

# The provider scans all .py files in the directory recursively.
# Functions decorated with @tool, @resource, or @prompt are registered.
# Directory structure is purely organizational - decorators determine type.
provider = FileSystemProvider(
    root=Path(__file__).parent / "mcp",
    reload=True,  # Set True for dev mode (re-scan on every request)
)

mcp = FastMCP("FilesystemDemo", providers=[provider])
