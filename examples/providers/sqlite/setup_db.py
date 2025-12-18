# /// script
# dependencies = ["aiosqlite"]
# ///
"""
Creates and seeds the tools database.

Run with: uv run examples/providers/sqlite/setup_db.py
"""

import asyncio
import json
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).parent / "tools.db"


async def setup_database() -> None:
    """Create the tools table and seed with example tools."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tools (
                name TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                parameters_schema TEXT NOT NULL,
                operation TEXT NOT NULL,
                default_value REAL,
                enabled INTEGER DEFAULT 1
            )
        """)

        tools_data = [
            (
                "add_numbers",
                "Add two numbers together",
                json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number", "description": "First number"},
                            "b": {"type": "number", "description": "Second number"},
                        },
                        "required": ["a", "b"],
                    }
                ),
                "add",
                0,
                1,
            ),
            (
                "multiply_numbers",
                "Multiply two numbers",
                json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number", "description": "First number"},
                            "b": {"type": "number", "description": "Second number"},
                        },
                        "required": ["a", "b"],
                    }
                ),
                "multiply",
                1,
                1,
            ),
            (
                "divide_numbers",
                "Divide two numbers",
                json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number", "description": "Dividend"},
                            "b": {"type": "number", "description": "Divisor"},
                        },
                        "required": ["a", "b"],
                    }
                ),
                "divide",
                0,
                1,
            ),
        ]

        await db.executemany(
            """
            INSERT OR REPLACE INTO tools
            (name, description, parameters_schema, operation, default_value, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            tools_data,
        )
        await db.commit()

    print(f"Database created at: {DB_PATH}")
    print("Seeded 3 tools: add_numbers, multiply_numbers, divide_numbers")


if __name__ == "__main__":
    asyncio.run(setup_database())
