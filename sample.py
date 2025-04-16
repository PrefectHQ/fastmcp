"""Sample code for FastMCP."""

from src.fastmcp import FastMCP
from src.fastmcp.utilities.registerable import (
    McpRegisterable,
    mcp_prompt,
    mcp_resource,
    mcp_tool,
)

mcp = FastMCP()


class Sample(McpRegisterable):
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
    mcp.run("sse")


if __name__ == "__main__":
    main()
