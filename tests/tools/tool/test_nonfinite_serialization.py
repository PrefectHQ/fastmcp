"""Non-finite floats in tool results must not be silently nulled."""

import math

import pytest
from pydantic import BaseModel, Field, field_serializer

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError


class Stats(BaseModel):
    mean: float


class StatsWithScratch(BaseModel):
    mean: float
    scratch: float = Field(exclude=True)


class StatsWithMissingSentinel(BaseModel):
    mean: float | str

    @field_serializer("mean", when_used="json")
    def _mean(self, value: float | str) -> float | str:
        if isinstance(value, float) and not math.isfinite(value):
            return "missing"
        return value


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


async def test_excluded_nonfinite_field_is_not_rejected():
    """A NaN that Pydantic omits from the wire must not fail serialization."""
    mcp = FastMCP("test")

    @mcp.tool
    def stats() -> StatsWithScratch:
        return StatsWithScratch(mean=1.5, scratch=float("nan"))

    async with Client(mcp) as client:
        result = await client.call_tool("stats", {})

    assert result.structured_content == {"mean": 1.5}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_visible_nonfinite_still_rejected_alongside_excluded_field(value: float):
    """Excluding one field must not hide a non-finite value that still serializes."""
    mcp = FastMCP("test")

    @mcp.tool
    def stats() -> StatsWithScratch:
        return StatsWithScratch(mean=value, scratch=float("nan"))

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("stats", {})

    assert "non-finite" in str(exc_info.value).lower()


async def test_json_field_serializer_rewritten_nan_is_not_rejected():
    """A JSON-only serializer that replaces NaN with a sentinel must pass."""
    mcp = FastMCP("test")

    @mcp.tool
    def stats() -> StatsWithMissingSentinel:
        return StatsWithMissingSentinel(mean=float("nan"))

    async with Client(mcp) as client:
        result = await client.call_tool("stats", {})

    assert result.structured_content == {"mean": "missing"}
