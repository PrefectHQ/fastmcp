"""Multiple choice — let the user pick from options instead of typing.

Usage:
    uv run python choice_server.py
"""

from fastmcp import FastMCP
from fastmcp.apps.choice import Choice

mcp = FastMCP("Choice Demo", providers=[Choice()])

if __name__ == "__main__":
    mcp.run()
