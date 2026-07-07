"""Tests for FastMCP's exception-to-wire-error translation."""

from __future__ import annotations

import pytest
from mcp import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS

from fastmcp import Client, FastMCP
from fastmcp.exceptions import (
    DisabledError,
    NotFoundError,
    PromptError,
    ResourceError,
    ToolError,
    ValidationError,
    to_mcp_error,
)


class TestToMcpError:
    @pytest.mark.parametrize(
        "exc",
        [NotFoundError("missing"), DisabledError("off"), ValidationError("bad")],
    )
    def test_invalid_params_mapping(self, exc: Exception):
        """Not-found, disabled, and validation errors map to INVALID_PARAMS.

        SEP-2164 defines a request naming a nonexistent (or disabled) component
        as an invalid-params error, matching the SDK's own mcpserver mapping.
        """
        result = to_mcp_error(exc)
        assert isinstance(result, MCPError)
        assert result.error.code == INVALID_PARAMS
        assert result.error.message == str(exc)

    @pytest.mark.parametrize(
        "exc",
        [ResourceError("boom"), PromptError("boom"), ToolError("boom")],
    )
    def test_default_code_for_unmapped_errors(self, exc: Exception):
        """Operation errors without a dedicated mapping use the default code."""
        assert to_mcp_error(exc).error.code == INTERNAL_ERROR

    def test_custom_default_code(self):
        assert (
            to_mcp_error(ResourceError("x"), default_code=-32000).error.code == -32000
        )

    def test_existing_mcp_error_passes_through(self):
        """An MCPError chosen upstream survives translation unchanged."""
        existing = MCPError(code=-32000, message="explicit")
        assert to_mcp_error(existing) is existing


class TestWireErrorCodes:
    """The core request-handler adapters must emit spec-correct wire codes."""

    async def test_resource_not_found_uses_invalid_params(self):
        """Resource-not-found is INVALID_PARAMS (-32602), not -32002.

        SEP-2164 corrected this: the SDK's mcpserver maps ResourceNotFoundError
        to INVALID_PARAMS. FastMCP previously deviated with -32002.
        """
        mcp = FastMCP("test-server")

        async with Client(mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.read_resource_mcp("config://missing")

        assert exc_info.value.error.code == INVALID_PARAMS
        assert "Resource not found" in exc_info.value.error.message

    async def test_prompt_not_found_uses_invalid_params(self):
        mcp = FastMCP("test-server")

        async with Client(mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.get_prompt("missing", {})

        assert exc_info.value.error.code == INVALID_PARAMS
        assert "Unknown prompt" in exc_info.value.error.message
