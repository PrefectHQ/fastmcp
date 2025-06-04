"""
FastMCP Echo Server
"""

from fastmcp import FastMCP

# Create server
mcp = FastMCP("Echo Server")


@mcp.tool
def echo(text: str) -> str:
    """Echo the input text"""
    return text
