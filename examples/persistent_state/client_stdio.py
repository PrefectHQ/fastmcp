"""Client for testing persistent state over STDIO.

Run directly:
    uv run python examples/persistent_state/client_stdio.py
"""

import asyncio
import sys
from pathlib import Path

from rich.console import Console

from fastmcp import Client, FastMCP

# Add parent directory to path for importing the server module
examples_dir: Path = Path(__file__).parent.parent.parent
if str(examples_dir) not in sys.path:
    sys.path.insert(0, str(examples_dir))

import examples.persistent_state.server as server_module  # noqa: E402

server: FastMCP = server_module.server

console: Console = Console()


async def main() -> None:
    console.print()
    console.print("[dim italic]Each line below is a separate tool call[/dim italic]")
    console.print()

    # --- Alice's session ---
    console.print("[dim]Alice connects[/dim]")

    async with Client(server) as alice:
        result = await alice.call_tool("list_session_info", {})
        console.print(f"  session [cyan]{result.data['session_id'][:8]}[/cyan]")

        await alice.call_tool("set_value", {"key": "user", "value": "Alice"})
        console.print("  set [white]user[/white] = [green]Alice[/green]")

        await alice.call_tool("set_value", {"key": "secret", "value": "alice-password"})
        console.print("  set [white]secret[/white] = [green]alice-password[/green]")

        await alice.call_tool("get_value", {"key": "user"})
        console.print("  get [white]user[/white] → [green]Alice[/green]")

        await alice.call_tool("get_value", {"key": "secret"})
        console.print("  get [white]secret[/white] → [green]alice-password[/green]")

    console.print()

    # --- Bob's session ---
    console.print("[dim]Bob connects (different session)[/dim]")

    async with Client(server) as bob:
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

    async with Client(server) as alice_again:
        result = await alice_again.call_tool("list_session_info", {})
        console.print(f"  session [cyan]{result.data['session_id'][:8]}[/cyan]")

        await alice_again.call_tool("get_value", {"key": "user"})
        console.print("  get [white]user[/white] → [dim]not found[/dim]")

    console.print()


if __name__ == "__main__":
    asyncio.run(main())
