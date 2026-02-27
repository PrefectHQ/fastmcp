"""Example: Client using CodeMode to discover and chain tools.

CodeMode exposes just two tools: `search` (keyword query) and `execute`
(run Python code with `call_tool` available). This client demonstrates
both: searching for tools, then chaining multiple calls in a single
execute block — one round-trip instead of many.

Run with:
    uv run python examples/code_mode/client.py
"""

import asyncio
import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
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
    async with Client("examples/code_mode/server.py") as client:
        console.print()
        console.rule("[bold]CodeMode[/bold]")
        console.print()

        # Step 1: list_tools only returns two synthetic meta-tools
        console.print(
            "The server has 8 tools. CodeMode replaces them with "
            "two synthetic tools — [bold]search[/bold] and [bold]execute[/bold]:"
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

        # Step 2: search discovers available tools
        console.print("The LLM calls [bold]search[/bold] to discover available tools:")
        console.print()
        result = await client.call_tool("search", {"query": "add multiply numbers"})
        found = _get_result(result)
        if isinstance(found, str):
            found = json.loads(found)
        console.print(
            Panel(
                _tool_table(found, ranked=True, show_params=True),
                title='[bold]search[/bold]  [dim]query="add multiply numbers"[/dim]',
                title_align="left",
                border_style="green",
            )
        )
        console.print()

        # Step 3: execute chains tool calls in one round-trip
        console.print(
            "Now the LLM writes a Python script that chains "
            "the tools it found. All of it runs server-side in a "
            "sandbox — [bold]one round-trip[/bold], intermediate "
            "data never hits the context window:"
        )
        console.print()
        code = """\
a = await call_tool("add", {"a": 3, "b": 4})
b = await call_tool("multiply", {"x": a["result"], "y": 2})
fib = await call_tool("fibonacci", {"n": b["result"]})
return {"sum": a["result"], "product": b["result"], "fibonacci": fib["result"]}
"""
        result = await client.call_tool("execute", {"code": code})
        console.print(
            Panel(
                Syntax(code.strip(), "python", theme="monokai"),
                title="[bold]execute[/bold]",
                title_align="left",
                border_style="yellow",
            )
        )
        console.print()

        # Final result
        console.print(f"  Result: [bold green]{_get_result(result)}[/bold green]")
        console.print()


if __name__ == "__main__":
    asyncio.run(main())
