# Dynamic Tools from SQLite

This example demonstrates serving MCP tools from a database. Tools can be added, modified, or disabled by updating the database - no server restart required.

## Structure

- `tools.db` - SQLite database with tool configurations (committed for convenience)
- `setup_db.py` - Script to create/reset the database
- `server.py` - MCP server that loads tools from the database

## Usage

```bash
# Reset the database (optional - tools.db is pre-seeded)
uv run examples/providers/sqlite/setup_db.py

# Run the server
uv run fastmcp run examples/providers/sqlite/server.py
```

## How It Works

The `SQLiteToolProvider` queries the database on every `list_tools` and `call_tool` request:

```python
class SQLiteToolProvider(BaseToolProvider):
    async def list_tools(self) -> list[Tool]:
        # Query database for enabled tools
        ...

    async def get_tool(self, name: str) -> Tool | None:
        # Efficient single-tool lookup
        ...
```

Tools are defined as `ConfigurableTool` subclasses that combine schema and execution:

```python
class ConfigurableTool(Tool):
    operation: str  # "add", "multiply", etc.

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        # Execute based on configured operation
        ...
```

## Modifying Tools at Runtime

While the server is running, you can modify tools in the database:

```bash
# Add a new tool
sqlite3 examples/providers/sqlite/tools.db "INSERT INTO tools VALUES ('subtract_numbers', 'Subtract two numbers', '{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"number\"},\"b\":{\"type\":\"number\"}},\"required\":[\"a\",\"b\"]}', 'subtract', 0, 1)"

# Disable a tool
sqlite3 examples/providers/sqlite/tools.db "UPDATE tools SET enabled = 0 WHERE name = 'divide_numbers'"
```

The next `list_tools` or `call_tool` request will reflect these changes.
