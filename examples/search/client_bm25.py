"""Example: Client using BM25 search to discover and call tools.

BM25 search accepts natural language queries instead of regex patterns.
This client shows how relevance ranking surfaces the best matches.

Run with:
    uv run python examples/search/client_bm25.py
"""

import asyncio
import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fastmcp.client import Client

console = Console()


def _get_result(result) -> Any:
    """Extract the value from a CallToolResult (structured or text)."""
    if result.structured_content is not None:
        data = result.structured_content
        if isinstance(data, dict) and set(data) == {"result"}:
            return data["result"]
        return data
    return result.content[0].text


def _format_params(tool: dict) -> str:
    """Format inputSchema properties as a compact signature."""
    schema = tool.get("inputSchema", {})
    props = schema.get("properties", {})
    if not props:
        return "()"
    parts = []
    for name, info in props.items():
        typ = info.get("type", "")
        parts.append(f"{name}: {typ}" if typ else name)
    return f"({', '.join(parts)})"


def _tool_table(
    tools: list[dict], *, ranked: bool = False, show_params: bool = False
) -> Table:
    table = Table(show_header=True, show_edge=False, pad_edge=False, expand=True)
    if ranked:
        table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Tool", style="cyan", no_wrap=True)
    if show_params:
        table.add_column("Parameters", style="dim", no_wrap=True)
    table.add_column("Description", style="dim")
    for i, tool in enumerate(tools, 1):
        row = [tool["name"]]
        if show_params:
            row.append(_format_params(tool))
        row.append(tool.get("description", ""))
        if ranked:
            row.insert(0, str(i))
        table.add_row(*row)
    return table


async def main():
    async with Client("examples/search/server_bm25.py") as client:
        console.print()
        console.rule("[bold]BM25 Search Transform[/bold]")
        console.print()

        # Step 1: list_tools shows only synthetic tools + pinned tools
        console.print(
            "The server has 8 tools. BM25SearchTransform replaces them with "
            "just [bold]search_tools[/bold] and [bold]call_tool[/bold]. "
            "[bold]list_files[/bold] stays visible via [dim]always_visible[/dim]:"
        )
        console.print()
        tools = await client.list_tools()
        visible = [{"name": t.name, "description": t.description} for t in tools]
        console.print(
            Panel(
                _tool_table(visible),
                title="[bold]list_tools()[/bold]",
                title_align="left",
                border_style="blue",
            )
        )
        console.print()

        # Step 2: natural language search discovers tools by relevance
        console.print(
            "The LLM uses [bold]search_tools[/bold] with natural language "
            "to discover tools ranked by relevance:"
        )
        console.print()
        result = await client.call_tool("search_tools", {"query": "work with numbers"})
        found = _get_result(result)
        if isinstance(found, str):
            found = json.loads(found)
        console.print(
            Panel(
                _tool_table(found, ranked=True, show_params=True),
                title='[bold]search_tools[/bold]  [dim]query="work with numbers"[/dim]',
                title_align="left",
                border_style="green",
            )
        )
        console.print()

        result = await client.call_tool(
            "search_tools", {"query": "manipulate text strings"}
        )
        found = _get_result(result)
        if isinstance(found, str):
            found = json.loads(found)
        console.print(
            Panel(
                _tool_table(found, ranked=True, show_params=True),
                title='[bold]search_tools[/bold]  [dim]query="manipulate text strings"[/dim]',
                title_align="left",
                border_style="green",
            )
        )
        console.print()

        # Step 3: call a discovered tool
        console.print(
            "Then the LLM calls a discovered tool through [bold]call_tool[/bold]:"
        )
        console.print()
        result = await client.call_tool(
            "call_tool",
            {
                "name": "word_count",
                "arguments": {"text": "BM25 search makes tool discovery easy"},
            },
        )
        console.print(
            Panel(
                f'call_tool(name="word_count", arguments={{"text": "BM25 search makes tool discovery easy"}})\n→ [bold green]{_get_result(result)}[/bold green]',
                title="[bold]call_tool()[/bold]",
                title_align="left",
                border_style="magenta",
            )
        )
        console.print()


if __name__ == "__main__":
    asyncio.run(main())
