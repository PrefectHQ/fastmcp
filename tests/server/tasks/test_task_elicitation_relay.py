"""Tests for background task elicitation relay (notifications.py).

The relay bridges distributed background tasks to clients via the standard
MCP elicitation/create protocol. When a worker calls ctx.elicit(), the
notification subscriber detects the input_required notification and sends
an elicitation/create request to the client session. The client's
elicitation_handler fires, and the relay pushes the response to Redis
for the blocked worker.

These tests use Client(mcp) with the real memory:// Docket backend.
"""

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel

from fastmcp import FastMCP
from fastmcp.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.server.context import Context
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)


class TestElicitationRelay:
    """E2E tests for elicitation flowing through the standard MCP protocol."""

    async def test_accept_via_elicitation_handler(self):
        """Tool elicits, client handler accepts, tool gets the value."""
        mcp = FastMCP("relay-accept")

        @mcp.tool(task=True)
        async def ask_name(ctx: Context) -> str:
            result = await ctx.elicit("What is your name?", str)
            if isinstance(result, AcceptedElicitation):
                return f"Hello, {result.data}!"
            return "No name"

        async def handler(message, response_type, params, ctx):
            assert message == "What is your name?"
            return ElicitResult(action="accept", content={"value": "Alice"})

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("ask_name", {}, task=True)
            result = await task.result()
            assert result.data == "Hello, Alice!"

    async def test_decline_via_elicitation_handler(self):
        """Tool elicits, client handler declines, tool gets DeclinedElicitation."""
        mcp = FastMCP("relay-decline")

        @mcp.tool(task=True)
        async def optional_input(ctx: Context) -> str:
            result = await ctx.elicit("Provide a name?", str)
            if isinstance(result, DeclinedElicitation):
                return "User declined"
            if isinstance(result, AcceptedElicitation):
                return f"Got: {result.data}"
            return "Cancelled"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="decline")

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("optional_input", {}, task=True)
            result = await task.result()
            assert result.data == "User declined"

    async def test_cancel_via_elicitation_handler(self):
        """Tool elicits, client handler cancels, tool gets CancelledElicitation."""
        mcp = FastMCP("relay-cancel")

        @mcp.tool(task=True)
        async def cancellable(ctx: Context) -> str:
            result = await ctx.elicit("Input?", str)
            if isinstance(result, CancelledElicitation):
                return "Cancelled"
            return "Not cancelled"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="cancel")

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("cancellable", {}, task=True)
            result = await task.result()
            assert result.data == "Cancelled"

    async def test_dataclass_round_trips_through_relay(self):
        """Structured dataclass type round-trips through the relay."""
        mcp = FastMCP("relay-dataclass")

        @dataclass
        class UserInfo:
            name: str
            age: int

        @mcp.tool(task=True)
        async def get_user(ctx: Context) -> str:
            result = await ctx.elicit("Provide user info", UserInfo)
            if isinstance(result, AcceptedElicitation):
                assert isinstance(result.data, UserInfo)
                return f"{result.data.name} is {result.data.age}"
            return "No info"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(action="accept", content={"name": "Bob", "age": 30})

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("get_user", {}, task=True)
            result = await task.result()
            assert result.data == "Bob is 30"

    async def test_pydantic_model_round_trips_through_relay(self):
        """Structured Pydantic model round-trips through the relay."""
        mcp = FastMCP("relay-pydantic")

        class Config(BaseModel):
            host: str
            port: int

        @mcp.tool(task=True)
        async def get_config(ctx: Context) -> str:
            result = await ctx.elicit("Server config?", Config)
            if isinstance(result, AcceptedElicitation):
                assert isinstance(result.data, Config)
                return f"{result.data.host}:{result.data.port}"
            return "No config"

        async def handler(message, response_type, params, ctx):
            return ElicitResult(
                action="accept", content={"host": "localhost", "port": 8080}
            )

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("get_config", {}, task=True)
            result = await task.result()
            assert result.data == "localhost:8080"

    async def test_multiple_sequential_elicitations(self):
        """Tool calls ctx.elicit() twice, both go through the relay."""
        mcp = FastMCP("relay-multi")

        @mcp.tool(task=True)
        async def two_questions(ctx: Context) -> str:
            r1 = await ctx.elicit("First name?", str)
            r2 = await ctx.elicit("Last name?", str)
            if isinstance(r1, AcceptedElicitation) and isinstance(
                r2, AcceptedElicitation
            ):
                return f"{r1.data} {r2.data}"
            return "Incomplete"

        call_count = 0

        async def handler(message, response_type, params, ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                assert message == "First name?"
                return ElicitResult(action="accept", content={"value": "Jane"})
            else:
                assert message == "Last name?"
                return ElicitResult(action="accept", content={"value": "Doe"})

        async with Client(mcp, elicitation_handler=handler) as client:
            task = await client.call_tool("two_questions", {}, task=True)
            result = await task.result()
            assert result.data == "Jane Doe"
            assert call_count == 2

    async def test_no_elicitation_handler_returns_cancel(self):
        """Without an elicitation_handler, the relay fails and task gets cancel."""
        mcp = FastMCP("relay-no-handler")

        @mcp.tool(task=True)
        async def needs_input(ctx: Context) -> str:
            result = await ctx.elicit("Input?", str)
            if isinstance(result, CancelledElicitation):
                return "Cancelled as expected"
            if isinstance(result, AcceptedElicitation):
                return f"Got: {result.data}"
            return "Other"

        async with Client(mcp) as client:
            task = await client.call_tool("needs_input", {}, task=True)
            result = await asyncio.wait_for(task.result(), timeout=15.0)
            assert result.data == "Cancelled as expected"
