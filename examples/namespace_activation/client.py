"""
Namespace Activation Client

Demonstrates how session-specific visibility works from the client perspective.
"""

import asyncio
import sys
from pathlib import Path

from rich import print
from rich.panel import Panel

from fastmcp import Client


def load_server():
    """Load the example server."""
    examples_dir = Path(__file__).parent
    if str(examples_dir) not in sys.path:
        sys.path.insert(0, str(examples_dir))

    import server as server_module

    return server_module.server


server = load_server()


def show_tools(tools: list, title: str) -> None:
    """Display available tools in a panel."""
    tool_names = [f"[cyan]{t.name}[/]" for t in tools]
    print(Panel(", ".join(tool_names) or "[dim]No tools[/]", title=title))


async def main():
    print("\n[bold]Namespace Activation Demo[/]\n")

    async with Client(server) as client:
        # Initially only activation tools are visible
        tools = await client.list_tools()
        show_tools(tools, "Initial Tools")

        # Activate finance namespace
        print("\n[yellow]→ Calling activate_finance()[/]")
        result = await client.call_tool("activate_finance", {})
        print(f"  [green]{result.data}[/]")

        tools = await client.list_tools()
        show_tools(tools, "After Activating Finance")

        # Use a finance tool
        print("\n[yellow]→ Calling get_market_data(symbol='AAPL')[/]")
        result = await client.call_tool("get_market_data", {"symbol": "AAPL"})
        print(f"  [green]{result.data}[/]")

        # Activate admin namespace too
        print("\n[yellow]→ Calling activate_admin()[/]")
        result = await client.call_tool("activate_admin", {})
        print(f"  [green]{result.data}[/]")

        tools = await client.list_tools()
        show_tools(tools, "After Activating Admin")

        # Deactivate all - back to defaults
        print("\n[yellow]→ Calling deactivate_all()[/]")
        result = await client.call_tool("deactivate_all", {})
        print(f"  [green]{result.data}[/]")

        tools = await client.list_tools()
        show_tools(tools, "After Deactivating All")


if __name__ == "__main__":
    asyncio.run(main())
