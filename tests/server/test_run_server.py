# from pathlib import Path
# from typing import TYPE_CHECKING, Any

# import pytest

# import fastmcp
# from fastmcp import FastMCP

# if TYPE_CHECKING:
#     pass

# USERS = [
#     {"id": "1", "name": "Alice", "active": True},
#     {"id": "2", "name": "Bob", "active": True},
#     {"id": "3", "name": "Charlie", "active": False},
# ]


# @pytest.fixture
# def fastmcp_server():
#     server = FastMCP("TestServer")

#     # --- Tools ---

#     @server.tool
#     def greet(name: str) -> str:
#         """Greet someone by name."""
#         return f"Hello, {name}!"

#     @server.tool
#     def add(a: int, b: int) -> int:
#         """Add two numbers together."""
#         return a + b

#     @server.tool
#     def error_tool():
#         """This tool always raises an error."""
#         raise ValueError("This is a test error")

#     # --- Resources ---

#     @server.resource(uri="resource://wave")
#     def wave() -> str:
#         return "👋"

#     @server.resource(uri="data://users")
#     async def get_users() -> list[dict[str, Any]]:
#         return USERS

#     @server.resource(uri="data://user/{user_id}")
#     async def get_user(user_id: str) -> dict[str, Any] | None:
#         return next((user for user in USERS if user["id"] == user_id), None)

#     # --- Prompts ---

#     @server.prompt
#     def welcome(name: str) -> str:
#         return f"Welcome to FastMCP, {name}!"

#     return server


# @pytest.fixture
# async def stdio_client():
#     # Find the stdio.py script path
#     base_dir = Path(__file__).parent
#     stdio_script = base_dir / "test_servers" / "stdio.py"

#     if not stdio_script.exists():
#         raise FileNotFoundError(f"Could not find stdio.py script at {stdio_script}")

#     client = fastmcp.Client(
#         transport=fastmcp.client.transports.StdioTransport(
#             command="python",
#             args=[str(stdio_script)],
#         )
#     )

#     async with client:
#         print("READY")
#         yield client
#         print("DONE")


# class TestRunServerStdio:
#     async def test_run_server_stdio(
#         self, fastmcp_server: FastMCP, stdio_client: fastmcp.Client
#     ):
#         print("TEST")
#         tools = await stdio_client.list_tools()
#         print("TEST 2")
#         assert tools == 1


# class TestRunServerSSE:
#
#     async def test_run_server_sse(self, fastmcp_server: FastMCP):
#         pass
