"""Tests for deprecated elicitation behavior."""

from typing import Any, cast

import pytest

from fastmcp import Context, FastMCP
from fastmcp.client.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import FastMCPDeprecationWarning
from fastmcp.server.elicitation import AcceptedElicitation


async def test_elicitation_none_response_type_warns_deprecation():
    """Passing response_type=None is deprecated — warn at call time."""
    mcp = FastMCP("TestServer")

    @mcp.tool
    async def my_tool(context: Context) -> dict[str, Any]:
        with pytest.warns(FastMCPDeprecationWarning, match="response_type"):
            result = await context.elicit(message="", response_type=None)
        assert isinstance(result, AcceptedElicitation)
        return cast(dict[str, Any], result.data)

    async def elicitation_handler(message, response_type, params, ctx):
        return ElicitResult(action="accept", content={})

    async with Client(mcp, elicitation_handler=elicitation_handler) as client:
        await client.call_tool("my_tool", {})
