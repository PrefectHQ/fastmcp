"""Default text serialization must not silently turn non-finite floats into null."""

import json
import math
from decimal import Decimal
from fractions import Fraction
from numbers import Real
from typing import Annotated, Any, Literal

import pytest
from mcp_types import TextContent
from pydantic import BaseModel, ConfigDict, PlainSerializer, field_serializer

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import ToolResult, default_serializer


@Real.register
class ExternalReal:
    """An unsupported numeric scalar, like numpy.float32, without a dependency."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __float__(self) -> float:
        return self.value

    def __str__(self) -> str:
        return str(self.value)


class ExtendedFiniteReal(ExternalReal):
    """Finite scalar whose float coercion saturates outside the float range."""

    def __init__(self, sign: int) -> None:
        super().__init__(math.inf if sign > 0 else -math.inf)
        self.exact = Fraction(sign * 10**400)

    def __str__(self) -> str:
        return str(self.exact)

    def __lt__(self, other: float) -> bool:
        return self.exact < other

    def __gt__(self, other: float) -> bool:
        return self.exact > other


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("container", ["scalar", "dict", "list", "nested"])
def test_default_serializer_rejects_nonfinite(value: float, container: str) -> None:
    results: dict[str, Any] = {
        "scalar": value,
        "dict": {"mean": value},
        "list": [1.0, value],
        "nested": {"rows": [{"mean": value}]},
    }

    with pytest.raises(ToolError, match="non-finite"):
        default_serializer(results[container])


@pytest.mark.parametrize("output_schema", [None, {"type": "object"}])
async def test_tool_reports_nonfinite_text_as_an_error(
    output_schema: dict[str, Any] | None,
) -> None:
    server = FastMCP()

    @server.tool(output_schema=output_schema)
    def stats() -> dict[str, float]:
        return {"mean": math.nan}

    async with Client(server) as client:
        result = await client.call_tool("stats", {}, raise_on_error=False)

    assert result.is_error is True
    assert result.structured_content is None
    assert isinstance(result.content[0], TextContent)
    assert "non-finite" in result.content[0].text


def test_tool_result_rejects_nonfinite_text_content() -> None:
    with pytest.raises(ToolError, match="non-finite"):
        ToolResult(content={"mean": math.inf})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_text_fallback_rejects_unsupported_nonfinite_reals(value: float) -> None:
    with pytest.raises(ToolError, match="non-finite"):
        default_serializer({"mean": ExternalReal(value)})


async def test_unsupported_nonfinite_real_does_not_become_a_soft_success() -> None:
    server = FastMCP()

    @server.tool
    def stats() -> Any:
        return {"mean": ExternalReal(math.nan)}

    async with Client(server) as client:
        result = await client.call_tool("stats", {}, raise_on_error=False)

    assert result.is_error is True
    assert isinstance(result.content[0], TextContent)
    assert "non-finite" in result.content[0].text


def test_text_fallback_preserves_finite_reals() -> None:
    assert default_serializer(ExternalReal(2.5)) == '"2.5"'
    large = Fraction(10**400, 3)
    assert default_serializer(large) == f'"{large}"'


@pytest.mark.parametrize("sign", [-1, 1])
def test_text_fallback_preserves_extended_finite_reals(sign: int) -> None:
    value = ExtendedFiniteReal(sign)
    assert math.isinf(float(value))
    assert -math.inf < value < math.inf
    assert default_serializer({"mean": value}) == f'{{"mean":"{value}"}}'


async def test_extended_finite_real_does_not_become_a_tool_error() -> None:
    server = FastMCP()
    value = ExtendedFiniteReal(1)

    @server.tool
    def stats() -> Any:
        return {"mean": value}

    async with Client(server) as client:
        result = await client.call_tool("stats", {}, raise_on_error=False)

    assert result.is_error is False
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == f'{{"mean":"{value}"}}'


def test_default_serializer_preserves_finite_values_and_nan_strings() -> None:
    assert default_serializer({"mean": 2.5, "empty": None}) == (
        '{"mean":2.5,"empty":null}'
    )
    assert default_serializer({"note": "NaN, Infinity and -Infinity"}) == (
        '{"note":"NaN, Infinity and -Infinity"}'
    )


@pytest.mark.parametrize(
    ("key", "expected"),
    [(math.nan, "nan"), (math.inf, "inf"), (-math.inf, "-inf")],
)
def test_default_serializer_stringifies_nonfinite_keys(
    key: float, expected: str
) -> None:
    assert default_serializer({key: 1}) == f'{{"{expected}":1}}'


def test_default_serializer_keeps_distinct_nonfinite_keys() -> None:
    payload = default_serializer({math.nan: 1, math.inf: 2, -math.inf: 3})
    assert json.loads(payload) == {"nan": 1, "inf": 2, "-inf": 3}


def test_default_serializer_preserves_decimal_nan_string_encoding() -> None:
    assert default_serializer(Decimal("NaN")) == '"NaN"'


@pytest.mark.parametrize("policy", ["null", "strings"])
def test_default_serializer_honors_model_nonfinite_policy(
    policy: Literal["null", "strings"],
) -> None:
    class Report(BaseModel):
        model_config = ConfigDict(ser_json_inf_nan=policy)
        mean: float

    expected = '{"mean":null}' if policy == "null" else '{"mean":"NaN"}'
    assert default_serializer(Report(mean=math.nan)) == expected


def test_default_serializer_honors_custom_field_serializer_once() -> None:
    calls = []

    class Report(BaseModel):
        mean: float

        @field_serializer("mean")
        def serialize_mean(self, value: float) -> str:
            calls.append(value)
            return "NaN" if not math.isfinite(value) else str(value)

    assert default_serializer(Report(mean=math.nan)) == '{"mean":"NaN"}'
    assert len(calls) == 1


async def test_tool_honors_annotated_return_serializer() -> None:
    server = FastMCP()
    calls: list[float] = []

    def serialize(value: float) -> str:
        calls.append(value)
        return str(value)

    @server.tool
    def mean() -> Annotated[
        float, PlainSerializer(serialize, return_type=str, when_used="json")
    ]:
        return math.nan

    async with Client(server) as client:
        result = await client.call_tool("mean", {})

    assert result.is_error is False
    assert result.structured_content == {"result": "nan"}
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "nan"
    assert len(calls) == 1


async def test_untyped_tool_rejects_nonfinite_before_it_can_be_nulled() -> None:
    server = FastMCP()

    @server.tool
    def stats() -> Any:
        return {"mean": math.inf}

    async with Client(server) as client:
        result = await client.call_tool("stats", {}, raise_on_error=False)

    assert result.is_error is True
    assert isinstance(result.content[0], TextContent)
    assert "non-finite" in result.content[0].text


async def test_unknown_object_text_fallback_is_preserved() -> None:
    class Unknown:
        def __str__(self) -> str:
            return "NaN"

    server = FastMCP()

    @server.tool(output_schema=None)
    def unknown() -> Any:
        return Unknown()

    async with Client(server) as client:
        result = await client.call_tool("unknown", {})

    assert result.is_error is False
    assert result.structured_content is None
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == '"NaN"'
