"""
Version Filters for API Surfaces

Use VersionFilter to create distinct API surfaces from shared components.
This lets you serve v1, v2, v3 APIs from a single codebase.

Run: uv run python examples/versioning/version_filters.py
"""

import asyncio

from rich import print
from rich.table import Table

from fastmcp import Client, FastMCP
from fastmcp.server.providers import LocalProvider
from fastmcp.server.transforms import VersionFilter

# Shared component pool with all versions
components = LocalProvider()


@components.tool(version="1.0")
def process(data: str) -> str:
    """Process data (v1 - uppercase only)."""
    return data.upper()


@components.tool(version="2.0")
def process(data: str, mode: str = "upper") -> str:  # noqa: F811
    """Process data (v2 - with mode selection)."""
    return data.lower() if mode == "lower" else data.upper()


@components.tool(version="3.0")
def process(data: str, mode: str = "upper", repeat: int = 1) -> str:  # noqa: F811
    """Process data (v3 - with repeat)."""
    result = data.lower() if mode == "lower" else data.upper()
    return result * repeat


# Unversioned components pass through all filters
@components.tool()
def health() -> str:
    """Health check (always available)."""
    return "ok"


# Create filtered API surfaces
api_v1 = FastMCP("API v1", providers=[components])
api_v1.add_transform(VersionFilter(version_lt="2.0"))

api_v2 = FastMCP("API v2", providers=[components])
api_v2.add_transform(VersionFilter(version_gte="2.0", version_lt="3.0"))

api_v3 = FastMCP("API v3", providers=[components])
api_v3.add_transform(VersionFilter(version_gte="3.0"))


async def show_surface(name: str, server: FastMCP):
    """Show what's visible through a filtered server."""
    async with Client(server) as client:
        tools = await client.list_tools()

        table = Table(title=name)
        table.add_column("Tool")
        table.add_column("Version", style="green")

        for tool in tools:
            meta = tool.meta.get("fastmcp", {}) if tool.meta else {}
            table.add_row(tool.name, meta.get("version", "(unversioned)"))

        print(table)


async def main():
    # Show what each API surface exposes
    await show_surface("API v1 (version_lt='2.0')", api_v1)
    await show_surface("API v2 (version_gte='2.0', version_lt='3.0')", api_v2)
    await show_surface("API v3 (version_gte='3.0')", api_v3)

    # Same tool name, different behavior per API
    print("\n[bold]Same call through different APIs:[/]")
    for name, server in [("v1", api_v1), ("v2", api_v2), ("v3", api_v3)]:
        async with Client(server) as client:
            result = await client.call_tool("process", {"data": "Hello"})
            print(f"  API {name}: process('Hello') -> '{result.data}'")


if __name__ == "__main__":
    asyncio.run(main())
