"""Example: Client using BM25 search to discover and call tools.

BM25 search accepts natural language queries instead of regex patterns.
This client shows how relevance ranking surfaces the best matches.

Run with:
    uv run python examples/search/client_bm25.py
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


def _tool_table(tools: list[dict], *, ranked: bool = False) -> Table:
    table = Table(show_header=True, show_edge=False, pad_edge=False, expand=True)
    if ranked:
        table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Description", style="dim")
    for i, tool in enumerate(tools, 1):
        row = [tool["name"], tool.get("description", "")]
        if ranked:
            row.insert(0, str(i))
        table.add_row(*row)
    return table


async def main():
    async with Client("examples/search/server_bm25.py") as client:
        console.print()
        console.rule("[bold]BM25 Search Transform[/bold]")
        console.print()

        # list_files is pinned via always_visible
        tools = await client.list_tools()
        visible = [{"name": t.name, "description": t.description} for t in tools]
        console.print(
            Panel(
                _tool_table(visible),
                title="[bold]list_tools()[/bold]",
                subtitle="[dim]list_files pinned via always_visible[/dim]",
                border_style="blue",
            )
        )
        console.print()

        # Natural language searches — BM25 ranks by relevance
        queries = ["work with numbers", "manipulate text strings", "file operations"]
        for query in queries:
            result = await client.call_tool("search_tools", {"query": query})
            found = json.loads(_get_text(result))
            console.print(
                Panel(
                    _tool_table(found, ranked=True),
                    title=f'[bold]search_tools[/bold][dim](query="{query}")[/dim]',
                    subtitle=f"[dim]{len(found)} result{'s' if len(found) != 1 else ''}[/dim]",
                    border_style="green",
                )
            )
            console.print()

        # Call a discovered tool
        result = await client.call_tool(
            "call_tool",
            {
                "name": "word_count",
                "arguments": {"text": "BM25 search makes tool discovery easy"},
            },
        )
        call_label = Text.assemble(
            ("call_tool", "bold"),
            ('(word_count, text="BM25 search makes tool discovery easy")', "dim"),
        )
        console.print(call_label, "→", f"[bold green]{_get_text(result)}[/bold green]")
        console.print()


if __name__ == "__main__":
    asyncio.run(main())
