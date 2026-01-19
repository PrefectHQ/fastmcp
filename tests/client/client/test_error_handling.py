"""Client error handling tests."""

import pytest
from mcp.types import TextContent
from pydantic import AnyUrl

from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server.server import FastMCP


class TestErrorHandling:
    async def test_general_tool_exceptions_are_not_masked_by_default(self):
        mcp = FastMCP("TestServer")

        @mcp.tool
        def error_tool():
            raise ValueError("This is a test error (abc)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            result = await client.call_tool_mcp("error_tool", {})
            assert result.isError
            assert isinstance(result.content[0], TextContent)
            assert "test error" in result.content[0].text
            assert "abc" in result.content[0].text

    async def test_general_tool_exceptions_are_masked_when_enabled(self):
        mcp = FastMCP("TestServer", mask_error_details=True)

        @mcp.tool
        def error_tool():
            raise ValueError("This is a test error (abc)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            result = await client.call_tool_mcp("error_tool", {})
            assert result.isError
            assert isinstance(result.content[0], TextContent)
            assert "test error" not in result.content[0].text
            assert "abc" not in result.content[0].text

    async def test_validation_errors_are_not_masked_when_enabled(self):
        mcp = FastMCP("TestServer", mask_error_details=True)

        @mcp.tool
        def validated_tool(x: int) -> int:
            return x

        async with Client(transport=FastMCPTransport(mcp)) as client:
            result = await client.call_tool_mcp("validated_tool", {"x": "abc"})
            assert result.isError
            # Pydantic validation error message should NOT be masked
            assert isinstance(result.content[0], TextContent)
            assert "Input should be a valid integer" in result.content[0].text

    async def test_specific_tool_errors_are_sent_to_client(self):
        mcp = FastMCP("TestServer")

        @mcp.tool
        def custom_error_tool():
            raise ToolError("This is a test error (abc)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            result = await client.call_tool_mcp("custom_error_tool", {})
            assert result.isError
            assert isinstance(result.content[0], TextContent)
            assert "test error" in result.content[0].text
            assert "abc" in result.content[0].text

    async def test_general_resource_exceptions_are_not_masked_by_default(self):
        mcp = FastMCP("TestServer")

        @mcp.resource(uri="exception://resource")
        async def exception_resource():
            raise ValueError("This is an internal error (sensitive)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("exception://resource"))
            assert "Error reading resource" in str(excinfo.value)
            assert "sensitive" in str(excinfo.value)
            assert "internal error" in str(excinfo.value)

    async def test_general_resource_exceptions_are_masked_when_enabled(self):
        mcp = FastMCP("TestServer", mask_error_details=True)

        @mcp.resource(uri="exception://resource")
        async def exception_resource():
            raise ValueError("This is an internal error (sensitive)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("exception://resource"))
            assert "Error reading resource" in str(excinfo.value)
            assert "sensitive" not in str(excinfo.value)
            assert "internal error" not in str(excinfo.value)

    async def test_resource_errors_are_sent_to_client(self):
        mcp = FastMCP("TestServer")

        @mcp.resource(uri="error://resource")
        async def error_resource():
            raise ResourceError("This is a resource error (xyz)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("error://resource"))
            assert "This is a resource error (xyz)" in str(excinfo.value)

    async def test_general_template_exceptions_are_not_masked_by_default(self):
        mcp = FastMCP("TestServer")

        @mcp.resource(uri="exception://resource/{id}")
        async def exception_resource(id: str):
            raise ValueError("This is an internal error (sensitive)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("exception://resource/123"))
            assert "Error reading resource" in str(excinfo.value)
            assert "sensitive" in str(excinfo.value)
            assert "internal error" in str(excinfo.value)

    async def test_general_template_exceptions_are_masked_when_enabled(self):
        mcp = FastMCP("TestServer", mask_error_details=True)

        @mcp.resource(uri="exception://resource/{id}")
        async def exception_resource(id: str):
            raise ValueError("This is an internal error (sensitive)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("exception://resource/123"))
            assert "Error reading resource" in str(excinfo.value)
            assert "sensitive" not in str(excinfo.value)
            assert "internal error" not in str(excinfo.value)

    async def test_template_errors_are_sent_to_client(self):
        mcp = FastMCP("TestServer")

        @mcp.resource(uri="error://resource/{id}")
        async def error_resource(id: str):
            raise ResourceError("This is a resource error (xyz)")

        client = Client(transport=FastMCPTransport(mcp))

        async with client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(AnyUrl("error://resource/123"))
            assert "This is a resource error (xyz)" in str(excinfo.value)
