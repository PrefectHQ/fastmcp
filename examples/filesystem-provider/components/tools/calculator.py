"""Math tools with custom metadata."""

from fastmcp.tools import tool


@tool(
    name="add-numbers",  # Custom name (default would be "add")
    description="Add two numbers together.",
    tags={"math", "arithmetic"},
)
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


@tool(tags={"math", "arithmetic"})
def multiply(a: float, b: float) -> float:
    """Multiply two numbers.

    Args:
        a: First number.
        b: Second number.
    """
    return a * b
