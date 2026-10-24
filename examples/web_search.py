"""Example: Keyless web search and page fetching with a FastMCP client.

Connects to the Parallel Search MCP server over Streamable HTTP and walks
through the workflow from the HTTP transport guide one step further:
discover the tools, run a web search, then fetch the top result's content.

The server is free to use anonymously — no account or API key is required.
Anonymous searches run in Fast mode with lower rate limits. To use your own
higher limits, set a Parallel API key and it is sent as a Bearer <redacted>

    (export a Parallel API key as PARALLEL_API_KEY first)

Run with:
    uv run python examples/web_search.py
    uv run python examples/web_search.py "latest FastMCP release notes"
"""

import asyncio
import json
import os
import sys
import uuid
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "https://search.parallel.ai/mcp"

console = Console()


def _get_result(result) -> Any:
    """Extract the value from a CallToolResult (structured or text)."""
    if result.structured_content is not None:
        return result.structured_content
    text = result.content[0].text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _excerpt(result: dict, limit: int = 500) -> str:
    """Collapse a result's first excerpt to a one-paragraph preview."""
    excerpts = result.get("excerpts") or []
    text = excerpts[0] if excerpts else result.get("full_content", "")
    text = " ".join(str(text).split())
    return text[:limit] + ("…" if len(text) > limit else "")


async def main(objective: str) -> None:
    authenticated = bool(os.environ.get("PARALLEL_API_KEY"))
    transport = StreamableHttpTransport(URL, auth=os.environ.get("PARALLEL_API_KEY"))
    # One session id per conversation, reused across search and fetch calls;
    # the free tier uses it for rate limiting and log correlation.
    session_id = uuid.uuid4().hex

    async with Client(transport) as client:
        console.print()
        console.rule("[bold]Keyless Web Search[/bold]")
        console.print()
        console.print(
            f"Connected to [cyan]{URL}[/cyan] "
            f"({'authenticated' if authenticated else 'anonymous, free tier'})."
        )
        console.print()

        # Step 1: discover the remote server's tools
        tools = await client.list_tools()
        table = Table(show_header=True, show_edge=False, pad_edge=False)
        table.add_column("Tool", style="cyan", no_wrap=True)
        table.add_column("Description", style="dim")
        for tool in tools:
            description = (tool.description or "").split("\n")[0]
            table.add_row(tool.name, description)
        console.print(
            Panel(table, title="[bold]list_tools()[/bold]", title_align="left")
        )
        console.print()

        # Step 2: search the web
        console.print(f"Searching for: [bold]{objective}[/bold]")
        console.print()
        result = await client.call_tool(
            "web_search",
            {
                "objective": objective,
                "search_queries": [
                    "FastMCP latest release",
                    "FastMCP release notes",
                ],
                "session_id": session_id,
            },
        )
        search = _get_result(result)
        results = search.get("results", []) if isinstance(search, dict) else []
        if not results:
            console.print("[red]No results returned.[/red]")
            return

        table = Table(show_header=True, show_edge=False, pad_edge=False)
        table.add_column("#", style="dim", width=3, justify="right")
        table.add_column("Title", style="cyan")
        table.add_column("Published", style="dim", no_wrap=True)
        table.add_column("URL", style="dim")
        for i, item in enumerate(results[:5], 1):
            table.add_row(
                str(i),
                item.get("title", ""),
                item.get("publish_date", ""),
                item.get("url", ""),
            )
        console.print(
            Panel(
                table,
                title="[bold]web_search[/bold]",
                title_align="left",
                border_style="green",
            )
        )
        console.print()
        console.print(_excerpt(results[0]))
        console.print()

        # Step 3: fetch the top result's full content
        top_url = results[0]["url"]
        console.print(f"Fetching the top result: [cyan]{top_url}[/cyan]")
        console.print()
        result = await client.call_tool(
            "web_fetch",
            {
                "urls": [top_url],
                "objective": objective,
                "session_id": session_id,
            },
        )
        fetched = _get_result(result)
        pages = fetched.get("results", []) if isinstance(fetched, dict) else []
        if pages:
            console.print(
                Panel(
                    _excerpt(pages[0], limit=800),
                    title="[bold]web_fetch[/bold]",
                    title_align="left",
                    border_style="magenta",
                )
            )
        console.print()


if __name__ == "__main__":
    objective = (
        " ".join(sys.argv[1:])
        or "Find the latest release notes for the FastMCP Python framework"
    )
    asyncio.run(main(objective))
