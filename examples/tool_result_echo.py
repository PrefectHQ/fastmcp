"""
FastMCP Echo Server
"""

from dataclasses import dataclass

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult

mcp = FastMCP("Echo Server")


@dataclass
class EchoData:
    data: str


@mcp.tool
def echo(text: str) -> ToolResult:
    return ToolResult(
        content=text, structured_content=EchoData(data=text), meta={"some": "metadata"}
    )
