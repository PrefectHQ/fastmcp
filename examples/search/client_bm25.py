"""Example: Client using BM25 search to discover and call tools.

BM25 search accepts natural language queries instead of regex patterns.
This client shows how relevance ranking surfaces the best matches.

Run with:
    uv run python examples/search/client_bm25.py
"""

import asyncio
import json

from fastmcp.client import Client


def _get_text(result) -> str:
    """Extract text content from a CallToolResult."""
    return result.content[0].text


async def main():
    async with Client("examples/search/server_bm25.py") as client:
        # list_files is pinned via always_visible, so it appears alongside
        # the synthetic search/call tools
        print("=== Available Tools ===")
        tools = await client.list_tools()
        for tool in tools:
            print(f"  - {tool.name}: {tool.description}")
        print()

        # Natural language search — BM25 ranks by relevance
        print("=== Search: 'work with numbers' ===")
        result = await client.call_tool("search_tools", {"query": "work with numbers"})
        for tool in json.loads(_get_text(result)):
            print(f"  - {tool['name']}: {tool.get('description', '')}")
        print()

        print("=== Search: 'manipulate text strings' ===")
        result = await client.call_tool(
            "search_tools", {"query": "manipulate text strings"}
        )
        for tool in json.loads(_get_text(result)):
            print(f"  - {tool['name']}: {tool.get('description', '')}")
        print()

        print("=== Search: 'file operations' ===")
        result = await client.call_tool("search_tools", {"query": "file operations"})
        for tool in json.loads(_get_text(result)):
            print(f"  - {tool['name']}: {tool.get('description', '')}")
        print()

        # Call a discovered tool
        print("=== Calling 'word_count' via call_tool ===")
        result = await client.call_tool(
            "call_tool",
            {
                "name": "word_count",
                "arguments": {"text": "BM25 search makes tool discovery easy"},
            },
        )
        print(f"  Result: {_get_text(result)}")


if __name__ == "__main__":
    asyncio.run(main())
