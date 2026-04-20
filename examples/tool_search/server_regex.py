"""Example: Search transforms with regex pattern matching.

When a server has many tools, listing them all at once can overwhelm an LLM's
context window. Search transforms collapse the full tool catalog behind a
search interface — clients see only `search_tools` and `call_tool`, and
discover the real tools on demand.

This example registers a handful of tools and applies RegexSearchTransform.
Clients use `search_tools` with a regex pattern to find relevant tools, then
`call_tool` to execute them by name.

Run with:
    uv run python examples/tool_search/server_regex.py
"""

from fastmcp import FastMCP
from fastmcp.server.plugins.tool_search import ToolSearch, ToolSearchConfig

mcp = FastMCP(
    "Regex Search Demo",
    plugins=[ToolSearch(ToolSearchConfig(strategy="regex", max_results=3))],
)


# Register a variety of tools across different domains.
# With the search transform active, none of these appear in list_tools —
# they're only discoverable via search.


@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


@mcp.tool
def multiply(x: float, y: float) -> float:
    """Multiply two numbers."""
    return x * y


@mcp.tool
def fibonacci(n: int) -> list[int]:
    """Generate the first n Fibonacci numbers."""
    if n <= 0:
        return []
    seq = [0, 1]
    while len(seq) < n:
        seq.append(seq[-1] + seq[-2])
    return seq[:n]


@mcp.tool
def reverse_string(text: str) -> str:
    """Reverse a string."""
    return text[::-1]


@mcp.tool
def word_count(text: str) -> int:
    """Count the number of words in a text."""
    return len(text.split())


@mcp.tool
def to_uppercase(text: str) -> str:
    """Convert text to uppercase."""
    return text.upper()


# The ToolSearch plugin is configured at server construction above —
# nothing else to wire here.


if __name__ == "__main__":
    mcp.run()
