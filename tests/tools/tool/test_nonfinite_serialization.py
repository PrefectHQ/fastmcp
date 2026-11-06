"""Non-finite float results must be rejected, not silently rewritten to null.

JSON cannot represent NaN or Infinity. Serializing them through the default
JSON contract turns them into ``null``, silently corrupting structured content
(#5150). The fix serializes through the real JSON layer and rejects any
non-finite float that survives it, while custom serializers and explicit
per-model policies keep control of their own output.
"""

import math
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, field_serializer

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import ToolResult, _serialize_to_jsonable


class TestNonFiniteStructuredContentRejected:
    async def test_nan_scalar_raises(self):
        mcp = FastMCP()

        @mcp.tool
        def scalar() -> float:
            return float("nan")

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="non-finite"):
                await client.call_tool("scalar", {})

    async def test_infinity_scalar_raises(self):
        mcp = FastMCP()

        @mcp.tool
        def scalar() -> float:
            return float("inf")

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="non-finite"):
                await client.call_tool("scalar", {})

    async def test_nan_in_dict_raises_with_value_and_path(self):
        mcp = FastMCP()

        @mcp.tool
        def stats() -> dict[str, float]:
            return {"mean": float("nan"), "count": 2.0}

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match=r"nan.*\$\.mean"):
                await client.call_tool("stats", {})

    async def test_nan_in_list_raises(self):
        mcp = FastMCP()

        @mcp.tool
        def values() -> list[float]:
            return [1.0, float("nan")]

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match="non-finite"):
                await client.call_tool("values", {})

    async def test_nan_in_nested_containers_raises(self):
        mcp = FastMCP()

        @mcp.tool
        def nested() -> dict[str, list[dict[str, float]]]:
            return {"rows": [{"v": 1.0}, {"v": float("inf")}]}

        async with Client(mcp) as client:
            with pytest.raises(ToolError, match=r"non-finite.*\$\.rows\[1\]\.v"):
                await client.call_tool("nested", {})


class TestFiniteValuesUnaffected:
    async def test_finite_structured_content_round_trips(self):
        mcp = FastMCP()

        @mcp.tool
        def stats() -> dict[str, float]:
            return {"mean": 2.5, "count": 2.0}

        async with Client(mcp) as client:
            result = await client.call_tool("stats", {})

        assert result.structured_content == {"mean": 2.5, "count": 2.0}

    async def test_nan_converted_by_custom_field_serializer_is_preserved(self):
        class Report(BaseModel):
            mean: float

            @field_serializer("mean")
            def _mean(self, value: float) -> str:
                return f"{value:.4f}"

        mcp = FastMCP()

        @mcp.tool
        def report() -> Report:
            return Report(mean=float("nan"))

        async with Client(mcp) as client:
            result = await client.call_tool("report", {})

        # The custom serializer deliberately converted the value to valid
        # JSON, so it must pass through untouched.
        assert result.structured_content == {"mean": "nan"}


class TestSerializeToJsonableUnit:
    def test_any_fallback_rejects_non_finite(self):
        with pytest.raises(ToolError, match="non-finite"):
            _serialize_to_jsonable({"v": float("nan")})

    def test_any_fallback_rejects_non_finite_nested(self):
        with pytest.raises(ToolError, match=r"non-finite.*\$\.a\[1\]"):
            _serialize_to_jsonable({"a": [1.0, math.inf]})

    def test_typed_annotation_rejects_non_finite(self):
        with pytest.raises(ToolError, match="non-finite"):
            _serialize_to_jsonable({"v": float("nan")}, dict[str, float])

    def test_finite_values_pass_through(self):
        value = {"a": [1.0, 2.5], "b": None, "c": "x"}
        assert _serialize_to_jsonable(value, dict[str, Any]) == value

    def test_string_mentioning_nan_is_not_rejected(self):
        value = {"note": "the value was NaN at index 0", "data": [1.0]}
        assert _serialize_to_jsonable(value) == value

    def test_explicit_model_null_policy_is_preserved(self):
        class Lenient(BaseModel):
            model_config = ConfigDict(ser_json_inf_nan="null")
            mean: float

        # An explicit per-model policy deliberately emits null, which is valid
        # JSON — the boundary must not second-guess it.
        assert _serialize_to_jsonable(Lenient(mean=float("nan")), Lenient) == {
            "mean": None
        }

    def test_tool_result_structured_content_rejects_non_finite(self):
        with pytest.raises(ToolError, match="non-finite"):
            ToolResult(structured_content={"v": float("nan")})


class TestDocumentedResidualBehavior:
    """Documented trade-offs that remain by design (see follow-up issues).

    These tests pin the *current* behavior so any future policy change is a
    conscious, reviewed decision rather than a silent drift.
    """

    async def test_default_base_model_policy_still_nulls_silently(self):
        """A plain BaseModel's embedded config wins over the outer constants policy.

        pydantic's per-model default (`ser_json_inf_nan="null"`) rewrites the
        non-finite float to valid-JSON ``null``, so the substring guard never
        fires and the caller gets ``{"mean": null}`` — not a ToolError. This is
        the documented judgment call: overriding a model's embedded config from
        outside is at odds with preserving model control.
        """

        class Stats(BaseModel):
            mean: float

        mcp = FastMCP()

        @mcp.tool
        def stats() -> Stats:
            return Stats(mean=float("nan"))

        async with Client(mcp) as client:
            # Not rejected at the serialization boundary — the model's own
            # default policy governed — so the null reaches the client and
            # fails *its* output-schema validation with an error that never
            # mentions nan.
            with pytest.raises(RuntimeError, match="None is not of type 'number'"):
                await client.call_tool("stats", {})

        # Pin the boundary behavior: no ToolError, just the model's null.
        assert _serialize_to_jsonable(Stats(mean=float("nan")), Stats) == {
            "mean": None
        }

    def test_default_serializer_rewrites_non_finite_to_null(self):
        """`default_serializer` (the text-content path) still nulls non-finites.

        It goes through pydantic's default JSON policy, so it can disagree with
        the structured path this PR fixed. Tracked as a follow-up.
        """
        from fastmcp.tools.base import default_serializer

        assert default_serializer({"mean": float("nan")}) == '{"mean":null}'

    async def test_unserializable_result_falls_back_to_text_content_softly(self):
        """A value the structured serializer cannot handle becomes content-only.

        `convert_result` catches the serialization error and returns text
        content with `is_error` false — a soft success. This is the leftover
        text-path risk: a non-finite value that fails structured serialization
        surfaces as text, never as a tool error (follow-up: fail the text path
        too, e.g. an `inf_nan_mode="error"`-style check).
        """

        class Unserializable:
            def __str__(self):
                return "nan"

        mcp = FastMCP()

        @mcp.tool
        def weird():
            return Unserializable()

        async with Client(mcp) as client:
            result = await client.call_tool("weird", {})

        assert result.is_error is False
        assert result.structured_content is None
        # The text content went through the default JSON serializer, which
        # stringifies the value ("nan" as a JSON string), with no error raised.
        assert result.content[0].text == '"nan"'
