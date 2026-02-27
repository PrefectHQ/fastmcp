"""Example: Client using regex search to discover and call tools.

Demonstrates the workflow: list tools (sees only search_tools + call_tool),
search for tools matching a regex pattern, then call a discovered tool.

Run with:
    uv run python examples/search/client_regex.py
"""

import asyncio
import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fastmcp.client import Client

console = Console()


def _get_text(result) -> str:
    """Extract text content from a CallToolResult."""
    return result.content[0].text


def _tool_table(tools: list[dict]) -> Table:
    table = Table(show_header=True, show_edge=False, pad_edge=False, expand=True)
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Description", style="dim")
    for tool in tools:
        table.add_row(tool["name"], tool.get("description", ""))
    return table


async def main():
    async with Client("examples/search/server_regex.py") as client:
        console.print()
        console.rule("[bold]Regex Search Transform[/bold]")
        console.print()

        # Show what the client actually sees
        tools = await client.list_tools()
        visible = [{"name": t.name, "description": t.description} for t in tools]
        console.print(
            Panel(
                _tool_table(visible),
                title="[bold]list_tools()[/bold]",
                subtitle="[dim]real tools are discoverable via search[/dim]",
                border_style="blue",
            )
        )
        console.print()

        # Search for math tools
        result = await client.call_tool(
            "search_tools", {"pattern": "add|multiply|fibonacci"}
        )
        found = json.loads(_get_text(result))
        console.print(
            Panel(
                _tool_table(found),
                title='[bold]search_tools[/bold][dim](pattern="add|multiply|fibonacci")[/dim]',
                border_style="green",
            )
        )
        console.print()

        # Search for text tools
        result = await client.call_tool("search_tools", {"pattern": "text|string|word"})
        found = json.loads(_get_text(result))
        console.print(
            Panel(
                _tool_table(found),
                title='[bold]search_tools[/bold][dim](pattern="text|string|word")[/dim]',
                border_style="green",
            )
        )
        console.print()

        # Call discovered tools via the proxy
        result = await client.call_tool(
            "call_tool", {"name": "add", "arguments": {"a": 17, "b": 25}}
        )
        call_label = Text.assemble(
            ("call_tool", "bold"),
            ("(add, a=17, b=25)", "dim"),
        )
        console.print(call_label, "→", f"[bold green]{_get_text(result)}[/bold green]")

        result = await client.call_tool(
            "call_tool",
            {"name": "reverse_string", "arguments": {"text": "hello world"}},
        )
        call_label = Text.assemble(
            ("call_tool", "bold"),
            ('(reverse_string, text="hello world")', "dim"),
        )
        console.print(call_label, "→", f"[bold green]{_get_text(result)}[/bold green]")
        console.print()


if __name__ == "__main__":
    asyncio.run(main())
