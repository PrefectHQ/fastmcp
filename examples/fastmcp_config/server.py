"""Example FastMCP server for demonstrating fastmcp.json configuration."""

from fastmcp import FastMCP

# Create the FastMCP server instance
mcp = FastMCP("Config Example Server")


@mcp.tool
def echo(text: str) -> str:
    """Echo the provided text back to the user."""
    return f"You said: {text}"


@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


@mcp.resource("config://example")
def get_example_config() -> str:
    """Return an example configuration."""
    return """
    This server is configured using fastmcp.json.
    
    The configuration file specifies:
    - Python version
    - Dependencies
    - Transport settings
    - Other runtime options
    """


# This allows the server to run with: fastmcp run server.py
if __name__ == "__main__":
    import asyncio

    asyncio.run(mcp.run_async())
