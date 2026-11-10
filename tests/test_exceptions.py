"""Tests for FastMCP's exception-to-wire-error translation."""

from __future__ import annotations

import pytest
from mcp import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS
from mcp_types.jsonrpc import MISSING_REQUIRED_CLIENT_CAPABILITY

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

    async def test_resource_not_found_echoes_uri_in_data(self):
        """SEP-2164 SHOULD: the error names which URI was missing.

        A client that pipelined several reads cannot otherwise tell which one
        failed from the message alone.
        """
        mcp = FastMCP("test-server")

        async with Client(mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.read_resource_mcp("config://missing")

        assert exc_info.value.error.data == {"uri": "config://missing"}

    async def test_prompt_not_found_uses_invalid_params(self):
        mcp = FastMCP("test-server")

        async with Client(mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.get_prompt("missing", {})

        assert exc_info.value.error.code == INVALID_PARAMS
        assert "Unknown prompt" in exc_info.value.error.message


class TestMissingClientCapabilityFromTool:
    """A tool's `-32021` must reach the wire, not become an `isError` result.

    SEP-2575 makes this error a statement about the *request* — the server
    cannot service it at all — so flattening it into a tool result would drop
    the code and tell the client the call succeeded. Every other `MCPError`
    raised under a tool still masks into a result, since those describe how the
    call went rather than whether it could run.
    """

    @staticmethod
    def _server() -> FastMCP:
        mcp = FastMCP("capability-test")

        @mcp.tool
        async def needs_sampling() -> str:
            raise MCPError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message="Client did not declare the required 'sampling' capability",
                data={"requiredCapabilities": {"sampling": {}}},
            )

        @mcp.tool
        async def upstream_failed() -> str:
            raise MCPError(code=INTERNAL_ERROR, message="upstream exploded")

        return mcp

    async def test_capability_error_propagates_as_protocol_error(self):
        async with Client(self._server()) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.call_tool("needs_sampling")

        assert exc_info.value.error.code == MISSING_REQUIRED_CLIENT_CAPABILITY
        assert exc_info.value.error.data == {"requiredCapabilities": {"sampling": {}}}

    async def test_other_mcp_errors_still_become_tool_errors(self):
        async with Client(self._server()) as client:
            with pytest.raises(ToolError):
                await client.call_tool("upstream_failed")
