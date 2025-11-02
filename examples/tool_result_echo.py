"""
FastMCP Echo Server with Metadata

Demonstrates how to return metadata alongside content and structured data.
The meta field can include execution details, versioning, or other information
that clients may find useful.
"""

import time
from dataclasses import dataclass

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult

mcp = FastMCP("Echo Server")


@dataclass
class EchoData:
    data: str
    length: int


@mcp.tool
def echo(text: str) -> ToolResult:
    """Echo text back with metadata about the operation."""
    start = time.perf_counter()

    result = EchoData(data=text, length=len(text))

    execution_time = (time.perf_counter() - start) * 1000

    return ToolResult(
        content=f"Echoed: {text}",
        structured_content=result,
        meta={
            "execution_time_ms": round(execution_time, 2),
            "character_count": len(text),
            "word_count": len(text.split()),
        },
    )
