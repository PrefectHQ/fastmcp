"""Sample code for FastMCP using MCPMixin."""

from contrib.mcp_mixin.mcp_mixin import (
    MCPMixin,
    mcp_prompt,
    mcp_resource,
    mcp_tool,
)
from fastmcp import FastMCP

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


def main():
    print("MCP Server running with registered components...")
    print("Tools:", list(mcp.get_tools().keys()))
    print("Resources:", list(mcp.get_resources().keys()))
    print("Prompts:", [p.name for p in mcp.list_prompts()])
    mcp.run()


if __name__ == "__main__":
    main()
