#!/usr/bin/env python3
"""Quick test to verify the revert worked correctly."""

import asyncio

from fastmcp import FastMCP
from fastmcp.client import Client


async def test_empty_prefix_behavior():
    """Test that empty prefix correctly adds underscore."""

    main_app = FastMCP("MainApp")
    sub_app = FastMCP("SubApp")

    @sub_app.tool
    def sub_tool() -> str:
        return "This is from the sub app"

    @sub_app.resource("data://test")
    def sub_resource():
        return "Resource data"

    # Mount with empty prefix
    main_app.mount("", sub_app)

    # Check that tools have underscore prefix
    tools = await main_app.get_tools()
    print(f"Tools: {list(tools.keys())}")
    assert "_sub_tool" in tools, f"Expected '_sub_tool' in {list(tools.keys())}"

    # Check that resources work correctly
    resources = await main_app.get_resources()
    print(f"Resources: {list(resources.keys())}")
    # Empty prefix for resources should result in no prefix change
    assert "data://test" in resources, (
        f"Expected 'data://test' in {list(resources.keys())}"
    )

    # Test calling the tool
    async with Client(main_app) as client:
        result = await client.call_tool("_sub_tool", {})
        print(f"Tool result: {result[0].text}")
        assert "This is from the sub app" in result[0].text

    print("✅ Empty prefix correctly adds underscore for tools!")


if __name__ == "__main__":
    asyncio.run(test_empty_prefix_behavior())
