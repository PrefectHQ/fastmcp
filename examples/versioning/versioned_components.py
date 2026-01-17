"""
Creating Versioned Components

Register multiple versions of the same tool, resource, or prompt.
Clients see the highest version by default but can request specific versions.

Run: uv run python examples/versioning/versioned_components.py
"""

import asyncio

from rich import print
from rich.table import Table

from fastmcp import Client, FastMCP

mcp = FastMCP("Versioned API")


# --- Versioned Tools ---
# Same name, different versions with different signatures


@mcp.tool(version="1.0")
def calculate(x: int, y: int) -> int:
    """Add two numbers (v1.0)."""
    return x + y


@mcp.tool(version="2.0")
def calculate(x: int, y: int, z: int = 0) -> int:  # noqa: F811
    """Add two or three numbers (v2.0)."""
    return x + y + z


# --- Versioned Resources ---
# Same URI, different content per version


@mcp.resource("config://app", version="1.0")
def config_v1() -> str:
    return '{"format": "legacy"}'


@mcp.resource("config://app", version="2.0")
def config_v2() -> str:
    return '{"format": "modern", "telemetry": true}'


# --- Versioned Prompts ---
# Same prompt, different templates per version


@mcp.prompt(version="1.0")
def summarize(text: str) -> str:
    return f"Summarize: {text}"


@mcp.prompt(version="2.0")
def summarize(text: str, style: str = "concise") -> str:  # noqa: F811
    return f"Summarize in a {style} style: {text}"


async def main():
    async with Client(mcp) as client:
        # List components - clients see highest version + all available versions
        tools = await client.list_tools()

        table = Table(title="Components (as seen by clients)")
        table.add_column("Type")
        table.add_column("Name")
        table.add_column("Version", style="green")
        table.add_column("All Versions", style="dim")

        for tool in tools:
            meta = tool.meta.get("fastmcp", {}) if tool.meta else {}
            table.add_row(
                "Tool",
                tool.name,
                meta.get("version"),
                ", ".join(meta.get("versions", [])),
            )

        print(table)

        # Call specific versions
        print("\n[bold]Calling specific versions:[/]")

        r_default = await client.call_tool("calculate", {"x": 5, "y": 3})
        r_v1 = await client.call_tool("calculate", {"x": 5, "y": 3}, version="1.0")
        r_v2 = await client.call_tool(
            "calculate", {"x": 5, "y": 3, "z": 2}, version="2.0"
        )

        print(f"  calculate(5, 3)          -> {r_default.data}  (default: highest)")
        print(f"  calculate(5, 3) v1.0     -> {r_v1.data}")
        print(f"  calculate(5, 3, 2) v2.0  -> {r_v2.data}")


if __name__ == "__main__":
    asyncio.run(main())
