"""Non-finite floats in tool results must not be silently nulled."""

import pytest
from pydantic import BaseModel

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError


class Stats(BaseModel):
    mean: float


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_plain_base_model_nonfinite_float_is_rejected(value: float):
    """A plain BaseModel result with a non-finite float must raise, not null."""
    mcp = FastMCP("test")

    @mcp.tool
    def stats() -> Stats:
        return Stats(mean=value)

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("stats", {})

    assert "non-finite" in str(exc_info.value).lower()


async def test_finite_base_model_float_still_serializes():
    mcp = FastMCP("test")

    @mcp.tool
    def stats() -> Stats:
        return Stats(mean=1.5)

    async with Client(mcp) as client:
        result = await client.call_tool("stats", {})

    assert result.structured_content == {"mean": 1.5}
