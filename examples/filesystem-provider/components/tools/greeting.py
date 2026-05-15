"""Greeting tools - multiple tools in one file."""

from fastmcp.tools import tool


@tool
def greet(name: str) -> str:
    """Greet someone by name.

    Args:
        name: The person's name.
    """
    return f"Hello, {name}!"


@tool
def farewell(name: str) -> str:
    """Say goodbye to someone.

    Args:
        name: The person's name.
    """
    return f"Goodbye, {name}!"


# Helper functions without decorators are ignored
def _format_message(msg: str) -> str:
    return msg.strip().capitalize()
