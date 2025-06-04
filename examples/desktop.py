"""
FastMCP Desktop Example

A simple example that exposes the desktop directory as a resource.
"""

from pathlib import Path

from fastmcp import FastMCP

# Create server
mcp = FastMCP("Demo")


@mcp.resource("dir://desktop")
def desktop() -> list[str]:
    """List the files in the user's desktop"""
    desktop = Path.home() / "Desktop"
    return [str(f) for f in desktop.iterdir()]


# Add a dynamic greeting resource
@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting"""
    return f"Hello, {name}!"


@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b
