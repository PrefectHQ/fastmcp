"""
Namespace Activation Server

Tools are organized into namespaces using tags, globally disabled by default,
and selectively enabled per-session via activation tools.
"""

from fastmcp import FastMCP
from fastmcp.server.context import Context

server = FastMCP("Multi-Domain Assistant")


# Finance namespace
@server.tool(tags={"namespace:finance"})
def analyze_portfolio(symbols: list[str]) -> str:
    """Analyze a portfolio of stock symbols."""
    return f"Portfolio analysis for: {', '.join(symbols)}"


@server.tool(tags={"namespace:finance"})
def get_market_data(symbol: str) -> dict:
    """Get current market data for a symbol."""
    return {"symbol": symbol, "price": 150.25, "change": "+2.5%"}


@server.tool(tags={"namespace:finance"})
def execute_trade(symbol: str, quantity: int, side: str) -> str:
    """Execute a trade (simulated)."""
    return f"Executed {side} order: {quantity} shares of {symbol}"


# Admin namespace
@server.tool(tags={"namespace:admin"})
def list_users() -> list[str]:
    """List all system users."""
    return ["alice", "bob", "charlie"]


@server.tool(tags={"namespace:admin"})
def reset_user_password(username: str) -> str:
    """Reset a user's password (simulated)."""
    return f"Password reset for {username}"


# Activation tools - always visible
@server.tool
async def activate_finance(ctx: Context) -> str:
    """Activate finance tools for this session."""
    await ctx.enable_components(tags={"namespace:finance"})
    return "Finance tools activated"


@server.tool
async def activate_admin(ctx: Context) -> str:
    """Activate admin tools for this session."""
    await ctx.enable_components(tags={"namespace:admin"})
    return "Admin tools activated"


@server.tool
async def deactivate_all(ctx: Context) -> str:
    """Deactivate all namespaces, returning to defaults."""
    await ctx.reset_visibility()
    return "All namespaces deactivated"


# Globally disable namespace tools by default
server.disable(tags={"namespace:finance", "namespace:admin"})


if __name__ == "__main__":
    server.run()
