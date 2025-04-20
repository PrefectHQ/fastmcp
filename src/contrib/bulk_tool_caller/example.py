"""Sample code for FastMCP using MCPMixin."""

from contrib.bulk_tool_caller import BulkToolCaller
from fastmcp import FastMCP

mcp = FastMCP()


@mcp.tool()
def echo_tool(text: str) -> str:
    """Echo the input text"""
    return text


bulk_tool_caller = BulkToolCaller()

bulk_tool_caller.register_tools(mcp)
