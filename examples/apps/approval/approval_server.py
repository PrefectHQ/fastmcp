"""Approval gate — require human sign-off before the agent acts.

Usage:
    uv run python approval_server.py
"""

from fastmcp import FastMCP
from fastmcp.apps.approval import Approval

mcp = FastMCP("Approval Demo", providers=[Approval()])

if __name__ == "__main__":
    mcp.run()
