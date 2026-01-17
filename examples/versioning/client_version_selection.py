"""
Client-Side Version Selection

Discover available versions via metadata and request specific versions
when calling tools, prompts, or resources.

Run: uv run python examples/versioning/client_version_selection.py
"""

import asyncio

from rich import print

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

mcp = FastMCP("Payment API")


@mcp.tool(version="1.0")
def charge(amount: int, currency: str = "USD") -> dict:
    """Charge a payment (v1.0 - basic)."""
    return {"status": "charged", "amount": amount, "currency": currency}


@mcp.tool(version="1.1")
def charge(  # noqa: F811
    amount: int, currency: str = "USD", idempotency_key: str | None = None
) -> dict:
    """Charge a payment (v1.1 - added idempotency)."""
    return {"status": "charged", "amount": amount, "idempotency_key": idempotency_key}


@mcp.tool(version="2.0")
def charge(  # noqa: F811
    amount: int,
    currency: str = "USD",
    idempotency_key: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Charge a payment (v2.0 - added metadata)."""
    return {"status": "charged", "amount": amount, "metadata": metadata or {}}


async def main():
    async with Client(mcp) as client:
        # Discover versions via metadata
        tools = await client.list_tools()
        tool = tools[0]
        meta = tool.meta.get("fastmcp", {})

        print(f"[bold]{tool.name}[/]")
        print(f"  Current version: [green]{meta.get('version')}[/]")
        print(f"  All versions:    {meta.get('versions')}")

        # Call specific versions
        print("\n[bold]Calling specific versions:[/]")

        r1 = await client.call_tool("charge", {"amount": 100}, version="1.0")
        print(f"  v1.0: {r1.data}")

        r1_1 = await client.call_tool(
            "charge", {"amount": 100, "idempotency_key": "abc"}, version="1.1"
        )
        print(f"  v1.1: {r1_1.data}")

        r2 = await client.call_tool(
            "charge", {"amount": 100, "metadata": {"order": "xyz"}}, version="2.0"
        )
        print(f"  v2.0: {r2.data}")

        # Handle missing versions
        print("\n[bold]Missing version:[/]")
        try:
            await client.call_tool("charge", {"amount": 100}, version="99.0")
        except ToolError as e:
            print(f"  [red]ToolError:[/] {e}")


if __name__ == "__main__":
    asyncio.run(main())
