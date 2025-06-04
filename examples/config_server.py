"""
Simple example showing FastMCP server with command line argument support.

Usage:
    fastmcp run examples/config_server.py -- --name MyServer --debug
"""

import argparse

from fastmcp import FastMCP

parser = argparse.ArgumentParser(description="Simple configurable MCP server")
parser.add_argument(
    "--name", type=str, default="ConfigurableServer", help="Server name"
)
parser.add_argument("--debug", action="store_true", help="Enable debug mode")

args = parser.parse_args()

server_name = args.name
if args.debug:
    server_name += " (Debug)"

mcp = FastMCP(server_name)


@mcp.tool
def get_status() -> dict[str, str | bool]:
    """Get the current server configuration and status."""
    return {
        "server_name": server_name,
        "debug_mode": args.debug,
        "original_name": args.name,
    }


@mcp.tool
def echo_message(message: str) -> str:
    """Echo a message, with debug info if debug mode is enabled."""
    if args.debug:
        return f"[DEBUG] Echoing: {message}"
    return message


if __name__ == "__main__":
    mcp.run()
