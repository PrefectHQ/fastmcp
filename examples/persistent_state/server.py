"""Example: Persistent session-scoped state.

This demonstrates using Context.get_state() and set_state() to store
data that persists across tool calls within the same MCP session.

Run with:
    uv run python examples/persistent_state/server.py
"""

from fastmcp import FastMCP
from fastmcp.server.context import Context

server = FastMCP("StateExample")


@server.tool
async def set_value(key: str, value: str, ctx: Context) -> str:
    """Store a value in session state."""
    await ctx.set_state(key, value)
    return f"Stored '{key}' = '{value}'"


@server.tool
async def get_value(key: str, ctx: Context) -> str:
    """Retrieve a value from session state."""
    value = await ctx.get_state(key)
    if value is None:
        return f"Key '{key}' not found"
    return f"'{key}' = '{value}'"


@server.tool
async def list_session_info(ctx: Context) -> dict[str, str | None]:
    """Get information about the current session."""
    return {
        "session_id": ctx.session_id,
        "transport": ctx.transport,
    }


if __name__ == "__main__":
    server.run(transport="streamable-http")
