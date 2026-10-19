"""Sample code for FastMCP using MCPMixin."""

import asyncio

from fastmcp import FastMCP
from fastmcp.contrib.mcp_mixin import (
    MCPMixin,
    mcp_prompt,
    mcp_resource,
    mcp_tool,
)

mcp = FastMCP()


class Sample(MCPMixin):
    def __init__(self, name):
        self.name = name

    @mcp_tool()
    def first_tool(self):
        """First tool description."""
        return f"Executed tool {self.name}."

    @mcp_resource(uri="test://test")
    def first_resource(self):
        """First resource description."""
        return f"Executed resource {self.name}."

    @mcp_prompt()
    def first_prompt(self):
        """First prompt description."""
        return f"here's a prompt! {self.name}."


first_sample = Sample("First")
second_sample = Sample("Second")

first_sample.register_all(mcp_server=mcp, prefix="first")
second_sample.register_all(mcp_server=mcp, prefix="second")


async def list_components() -> None:
    print("MCP Server running with registered components...")
    print("Tools:", list(await mcp.list_tools()))
    print("Resources:", list(await mcp.list_resources()))
    print("Prompts:", list(await mcp.list_prompts()))


if __name__ == "__main__":
    asyncio.run(list_components())
    mcp.run()
