"""Tests for distinguishing argument-validation errors from tool-body errors.

See https://github.com/PrefectHQ/fastmcp/issues/4128: a bad call (invalid
arguments) should surface as fastmcp's ``ValidationError`` so downstream error
taxonomy (middleware, Sentry filters) can treat it as a client error, while a
``pydantic.ValidationError`` raised by the tool's own body is a server-side bug
and must propagate unchanged.
"""

from typing import Annotated, Any

import pytest
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from fastmcp.exceptions import ValidationError
from fastmcp.tools.base import Tool


class _Inner(BaseModel):
    x: int


class TestArgumentValidationErrors:
    """Invalid arguments are converted to fastmcp's ValidationError."""

    async def test_async_constraint_violation(self):
        async def tool_fn(n: Annotated[int, Field(le=10)]) -> int:
            return n

        tool = Tool.from_function(tool_fn)
        with pytest.raises(ValidationError):
            await tool.run({"n": 20})

    async def test_sync_constraint_violation(self):
        def tool_fn(n: Annotated[int, Field(le=10)]) -> int:
            return n

        tool = Tool.from_function(tool_fn)
        with pytest.raises(ValidationError):
            await tool.run({"n": 20})

    async def test_wrong_type_is_argument_error(self):
        async def tool_fn(n: int) -> int:
            return n

        tool = Tool.from_function(tool_fn)
        with pytest.raises(ValidationError):
            await tool.run({"n": "not-an-int"})

    async def test_missing_required_argument(self):
        async def tool_fn(n: int) -> int:
            return n

        tool = Tool.from_function(tool_fn)
        with pytest.raises(ValidationError):
            await tool.run({})


class TestToolBodyErrors:
    """A pydantic error raised by the body is NOT reclassified as a bad call."""

    async def test_async_body_pydantic_error_propagates(self):
        async def tool_fn(data: str) -> int:
            bad_value: Any = "not-an-int"
            _Inner(x=bad_value)  # raises a pydantic ValidationError from the body
            return 1

        tool = Tool.from_function(tool_fn)
        with pytest.raises(PydanticValidationError):
            await tool.run({"data": "valid"})
        # And it must not be fastmcp's ValidationError.
        with pytest.raises(PydanticValidationError) as exc_info:
            await tool.run({"data": "valid"})
        assert not isinstance(exc_info.value, ValidationError)

    async def test_sync_body_pydantic_error_propagates(self):
        def tool_fn(data: str) -> int:
            bad_value: Any = "not-an-int"
            _Inner(x=bad_value)  # raises a pydantic ValidationError from the body
            return 1

        tool = Tool.from_function(tool_fn)
        with pytest.raises(PydanticValidationError):
            await tool.run({"data": "valid"})


class TestTaskArgumentValidation:
    """The task-execution path (coerce_task_arguments) converts arg errors too.

    The coercion logic moved to ``fastmcp_tasks.components`` during the
    SEP-1686 -> SEP-2663 migration, keyed by component type instead of being a
    method on the component.
    """

    def test_coerce_task_arguments_wrong_type(self):
        from fastmcp_tasks.components import coerce_task_arguments

        def tool_fn(n: int) -> int:
            return n

        tool = Tool.from_function(tool_fn)
        with pytest.raises(ValidationError):
            coerce_task_arguments(tool, {"n": "not-an-int"})

    def test_coerce_task_arguments_constraint_violation(self):
        from fastmcp_tasks.components import coerce_task_arguments

        def tool_fn(n: Annotated[int, Field(le=10)]) -> int:
            return n

        tool = Tool.from_function(tool_fn)
        with pytest.raises(ValidationError):
            coerce_task_arguments(tool, {"n": 20})


class TestValidCallsStillWork:
    """Regression: the happy path is unaffected."""

    async def test_async_valid_call(self):
        async def tool_fn(n: Annotated[int, Field(le=10)]) -> int:
            return n * 2

        tool = Tool.from_function(tool_fn)
        result = await tool.run({"n": 5})
        assert result.structured_content == {"result": 10}

    async def test_sync_valid_call(self):
        def tool_fn(n: int) -> int:
            return n + 1

        tool = Tool.from_function(tool_fn)
        result = await tool.run({"n": 5})
        assert result.structured_content == {"result": 6}
