from typing import Annotated, Literal

import pytest
from mcp import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS
from pydantic import BeforeValidator

from fastmcp import Client, FastMCP


class TestUnconvertibleArguments:
    """An argument that can't be converted to its type is a client error."""

    @pytest.mark.parametrize("mask_error_details", [False, True])
    @pytest.mark.parametrize(
        "arguments",
        [{"count": "zz", "mode": "a"}, {"count": "1", "mode": "zzz"}],
    )
    async def test_get_prompt_unconvertible_argument_is_invalid_params(
        self, mask_error_details: bool, arguments: dict[str, str]
    ):
        mcp = FastMCP(mask_error_details=mask_error_details)

        @mcp.prompt
        def summarize(count: int, mode: Literal["a", "b"]) -> str:
            return f"{count} {mode}"

        async with Client(mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.get_prompt("summarize", arguments)

        assert exc_info.value.error.code == INVALID_PARAMS
        assert exc_info.value.error.message.startswith("Could not convert argument")

    @pytest.mark.parametrize("value", ["1", "hello"])
    async def test_get_prompt_validator_type_error_remains_internal(self, value: str):
        def broken(value: str) -> int:
            raise TypeError("validator bug")

        mcp = FastMCP()

        @mcp.prompt
        def summarize(count: Annotated[int, BeforeValidator(broken)]) -> str:
            return str(count)

        async with Client(mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.get_prompt("summarize", {"count": value})

        assert exc_info.value.error.code == INTERNAL_ERROR
