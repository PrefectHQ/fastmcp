"""Example: CodeMode transform — search and execute tools via code.

CodeMode replaces the entire tool catalog with two meta-tools: `search`
(keyword-based tool discovery) and `execute` (run Python code that chains
tool calls in a sandbox). This dramatically reduces round-trips and
context window usage when an LLM needs to orchestrate many tools.

Requires pydantic-monty for the sandbox:
    pip install "fastmcp[code-mode]"

Run with:
    uv run python examples/code_mode/server.py
"""

from fastmcp import FastMCP
from fastmcp.experimental.transforms import CodeMode

mcp = FastMCP("CodeMode Demo")


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


# CodeMode collapses all 8 tools into just `search` + `execute`.
# The LLM discovers tools via keyword search, then writes Python
# scripts that chain multiple tool calls in a single round-trip.
mcp.add_transform(CodeMode())


if __name__ == "__main__":
    mcp.run()
