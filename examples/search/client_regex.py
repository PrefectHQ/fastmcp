"""Example: Client using regex search to discover and call tools.

Demonstrates the workflow: list tools (sees only search_tools + call_tool),
search for tools matching a regex pattern, then call a discovered tool.

Run with:
    uv run python examples/search/client_regex.py
"""

import asyncio
import json

from fastmcp.client import Client


def _get_text(result) -> str:
    """Extract text content from a CallToolResult."""
    return result.content[0].text


async def main():
    async with Client("examples/search/server_regex.py") as client:
        # The client only sees the synthetic search/call tools
        print("=== Available Tools ===")
        tools = await client.list_tools()
        for tool in tools:
            print(f"  - {tool.name}: {tool.description}")
        print()

        # Search for math-related tools using a regex pattern
        print("=== Search: math tools (pattern: 'add|multiply|fibonacci') ===")
        result = await client.call_tool(
            "search_tools", {"pattern": "add|multiply|fibonacci"}
        )
        for tool in json.loads(_get_text(result)):
            print(f"  - {tool['name']}: {tool.get('description', '')}")
        print()

        # Search for text-related tools
        print("=== Search: text tools (pattern: 'text|string|word') ===")
        result = await client.call_tool("search_tools", {"pattern": "text|string|word"})
        for tool in json.loads(_get_text(result)):
            print(f"  - {tool['name']}: {tool.get('description', '')}")
        print()

        # Call a discovered tool via the call_tool proxy
        print("=== Calling 'add' via call_tool ===")
        result = await client.call_tool(
            "call_tool", {"name": "add", "arguments": {"a": 17, "b": 25}}
        )
        print(f"  Result: {_get_text(result)}")
        print()

        print("=== Calling 'reverse_string' via call_tool ===")
        result = await client.call_tool(
            "call_tool",
            {"name": "reverse_string", "arguments": {"text": "hello world"}},
        )
        print(f"  Result: {_get_text(result)}")


if __name__ == "__main__":
    asyncio.run(main())
