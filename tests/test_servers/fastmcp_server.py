from typing import Any

from fastmcp import FastMCP

USERS = [
    {"id": "1", "name": "Alice", "active": True},
    {"id": "2", "name": "Bob", "active": True},
    {"id": "3", "name": "Charlie", "active": False},
]


server = FastMCP("TestServer")

# --- Tools ---


@server.tool()
def greet(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


@server.tool()
def error_tool():
    """This tool always raises an error."""
    raise ValueError("This is a test error")


# --- Resources ---


@server.resource(uri="resource://wave")
def wave() -> str:
    return "👋"


@server.resource(uri="data://users")
async def get_users() -> list[dict[str, Any]]:
    return USERS


@server.resource(uri="data://user/{user_id}")
async def get_user(user_id: str) -> dict[str, Any] | None:
    return next((user for user in USERS if user["id"] == user_id), None)


# --- Prompts ---


@server.prompt()
def welcome(name: str) -> str:
    return f"Welcome to FastMCP, {name}!"
