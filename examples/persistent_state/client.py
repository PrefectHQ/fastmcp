"""Client for testing persistent state.

Run the server first:
    uv run python examples/persistent_state/server.py

Then run this client:
    uv run python examples/persistent_state/client.py
"""

import asyncio

from rich.console import Console

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "http://127.0.0.1:8000/mcp"
console = Console()


async def main() -> None:
    console.print()
    console.print("[dim italic]Each line below is a separate tool call[/dim italic]")
    console.print()

    # --- Alice's session ---
    console.print("[dim]Alice connects[/dim]")

    transport1 = StreamableHttpTransport(url=URL)
    async with Client(transport=transport1) as alice:
        result = await alice.call_tool("list_session_info", {})
        console.print(f"  session [cyan]{result.data['session_id'][:8]}[/cyan]")

        await alice.call_tool("set_value", {"key": "user", "value": "Alice"})
        console.print("  set [white]user[/white] = [green]Alice[/green]")

        await alice.call_tool("set_value", {"key": "secret", "value": "alice-password"})
        console.print("  set [white]secret[/white] = [green]alice-password[/green]")

        result = await alice.call_tool("get_value", {"key": "user"})
        console.print("  get [white]user[/white] → [green]Alice[/green]")

        result = await alice.call_tool("get_value", {"key": "secret"})
        console.print("  get [white]secret[/white] → [green]alice-password[/green]")

    console.print()

    # --- Bob's session ---
    console.print("[dim]Bob connects (different session)[/dim]")

    transport2 = StreamableHttpTransport(url=URL)
    async with Client(transport=transport2) as bob:
        result = await bob.call_tool("list_session_info", {})
        console.print(f"  session [cyan]{result.data['session_id'][:8]}[/cyan]")

        await bob.call_tool("get_value", {"key": "user"})
        console.print("  get [white]user[/white] → [dim]not found[/dim]")

        await bob.call_tool("get_value", {"key": "secret"})
        console.print("  get [white]secret[/white] → [dim]not found[/dim]")

        await bob.call_tool("set_value", {"key": "user", "value": "Bob"})
        console.print("  set [white]user[/white] = [green]Bob[/green]")

        await bob.call_tool("get_value", {"key": "user"})
        console.print("  get [white]user[/white] → [green]Bob[/green]")

    console.print()

    # --- Alice reconnects ---
    console.print("[dim]Alice reconnects (new session)[/dim]")

    transport3 = StreamableHttpTransport(url=URL)
    async with Client(transport=transport3) as alice_again:
        result = await alice_again.call_tool("list_session_info", {})
        console.print(f"  session [cyan]{result.data['session_id'][:8]}[/cyan]")

        await alice_again.call_tool("get_value", {"key": "user"})
        console.print("  get [white]user[/white] → [dim]not found[/dim]")

    console.print()


if __name__ == "__main__":
    asyncio.run(main())
