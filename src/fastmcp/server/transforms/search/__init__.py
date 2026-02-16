"""Search transforms for tool discovery.

Search transforms collapse a large tool catalog into a search interface,
letting LLMs discover tools on demand instead of seeing the full list.

Example:
    ```python
    from fastmcp import FastMCP
    from fastmcp.server.transforms.search import RegexSearchTransform

    mcp = FastMCP("Server")
    mcp.add_transform(RegexSearchTransform())
    # list_tools now returns only search_tools + call_tool
    ```
"""

from fastmcp.server.transforms.search.bm25 import BM25SearchTransform
from fastmcp.server.transforms.search.regex import RegexSearchTransform

__all__ = [
    "BM25SearchTransform",
    "RegexSearchTransform",
]
