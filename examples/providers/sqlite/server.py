# /// script
# dependencies = ["aiosqlite", "fastmcp"]
# ///
"""
MCP server with database-configured tools.

Tools are loaded from tools.db on each request, so you can add/modify/disable
tools in the database without restarting the server.

Run with: uv run fastmcp run examples/providers/sqlite/server.py
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiosqlite
from rich import print

from fastmcp import Client, FastMCP
from fastmcp.server.providers import Provider
from fastmcp.tools.tool import Tool, ToolResult

DB_PATH = Path(__file__).parent / "tools.db"


class ConfigurableTool(Tool):
    """A tool that performs a configured arithmetic operation.

    This demonstrates the pattern: Tool subclass = schema + execution in one place.
    """

    operation: str  # "add", "multiply", "subtract", "divide"
    default_value: float = 0

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        a = arguments.get("a", self.default_value)
        b = arguments.get("b", self.default_value)

        if self.operation == "add":
            result = a + b
        elif self.operation == "multiply":
            result = a * b
        elif self.operation == "subtract":
            result = a - b
        elif self.operation == "divide":
            if b == 0:
                return ToolResult(
                    structured_content={
                        "error": "Division by zero",
                        "operation": self.operation,
                    }
                )
            result = a / b
        else:
            result = a + b

        return ToolResult(
            structured_content={"result": result, "operation": self.operation}
        )


class SQLiteToolProvider(Provider):
    """Queries SQLite for tool configurations.

    Called on every list_tools/get_tool request, so database changes
    are reflected immediately without server restart.
    """

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path

    async def list_tools(self) -> Sequence[Tool]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tools WHERE enabled = 1") as cursor:
                rows = await cursor.fetchall()
                return [self._make_tool(row) for row in rows]

    async def get_tool(self, name: str) -> Tool | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tools WHERE name = ? AND enabled = 1", (name,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._make_tool(row) if row else None

    def _make_tool(self, row: aiosqlite.Row) -> ConfigurableTool:
        return ConfigurableTool(
            name=row["name"],
            description=row["description"],
            parameters=json.loads(row["parameters_schema"]),
            operation=row["operation"],
            default_value=row["default_value"] or 0,
        )


provider = SQLiteToolProvider(db_path=str(DB_PATH))
mcp = FastMCP("DynamicToolsServer", providers=[provider])


@mcp.tool
def server_info() -> dict[str, str]:
    """Get information about this server (static tool)."""
    return {
        "name": "DynamicToolsServer",
        "description": "A server with database-configured tools",
        "database": str(DB_PATH),
    }


async def main():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print(f"[bold]Available tools ({len(tools)}):[/bold]")
        for tool in tools:
            print(f"  • {tool.name}: {tool.description}")

        print()
        print("[bold]Calling add_numbers(10, 5):[/bold]")
        result = await client.call_tool("add_numbers", {"a": 10, "b": 5})
        print(f"  Result: {result.structured_content}")

        print()
        print("[bold]Calling multiply_numbers(7, 6):[/bold]")
        result = await client.call_tool("multiply_numbers", {"a": 7, "b": 6})
        print(f"  Result: {result.structured_content}")


if __name__ == "__main__":
    asyncio.run(main())
