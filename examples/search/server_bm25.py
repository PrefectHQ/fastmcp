"""Example: Search transforms with BM25 relevance ranking.

BM25SearchTransform uses term-frequency/inverse-document-frequency scoring
to rank tools by relevance to a natural language query. Unlike regex search
(which requires the user to construct a pattern), BM25 handles queries like
"work with text" or "do math" and returns the most relevant matches.

The index is built lazily and rebuilt automatically when the tool catalog
changes (e.g. tools added or removed between requests).

Run with:
    uv run python examples/search/server_bm25.py
"""

from fastmcp import FastMCP
from fastmcp.server.transforms.search import BM25SearchTransform

mcp = FastMCP("BM25 Search Demo")


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


@mcp.tool
def list_files(directory: str) -> list[str]:
    """List files in a directory."""
    import os

    return os.listdir(directory)


@mcp.tool
def read_file(path: str) -> str:
    """Read the contents of a file."""
    with open(path) as f:
        return f.read()


# BM25 search with a higher result limit for this larger catalog.
# The `always_visible` option keeps specific tools in list_tools output
# alongside the search/call tools — useful for tools the LLM should
# always know about.
mcp.add_transform(BM25SearchTransform(max_results=5, always_visible=["list_files"]))


if __name__ == "__main__":
    mcp.run()
