"""Client error handling tests."""

import logging

import mcp.types
import pytest
from mcp.types import TextContent
from pydantic import AnyUrl

from fastmcp.client import Client
from fastmcp.client.mixins.tools import _parse_call_tool_result
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import PromptError, ResourceError, ToolError
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


class TestCallToolRaiseOnError:
    """Tests for call_tool error handling with raise_on_error."""

    async def test_call_tool_raises_tool_error_by_default(self):
        mcp = FastMCP("TestServer")

        @mcp.tool
        def failing_tool() -> str:
            raise ValueError("something broke")

        async with Client(transport=FastMCPTransport(mcp)) as client:
            with pytest.raises(ToolError, match="something broke"):
                await client.call_tool("failing_tool", {})

    async def test_call_tool_no_raise_returns_error_result(self):
        mcp = FastMCP("TestServer")

        @mcp.tool
        def failing_tool() -> str:
            raise ValueError("something broke")

        async with Client(transport=FastMCPTransport(mcp)) as client:
            result = await client.call_tool("failing_tool", {}, raise_on_error=False)
            assert result.is_error is True
            assert result.data is None


class TestParseToolResultEdgeCases:
    """Unit tests for _parse_call_tool_result with non-standard error payloads.

    These edge cases can't be triggered through the public Client API because
    FastMCP's server always produces TextContent on errors. Testing the parser
    directly covers defensive handling of third-party MCP servers.
    """

    async def test_error_with_empty_content_raises_with_fallback_message(self):
        result = mcp.types.CallToolResult(content=[], isError=True)

        with pytest.raises(ToolError, match="Tool 'my_tool' returned an error"):
            await _parse_call_tool_result(
                name="my_tool",
                result=result,
                tool_output_schemas={},
                list_tools_fn=None,
                raise_on_error=True,
            )

    async def test_error_with_non_text_content_raises_with_fallback_message(self):
        result = mcp.types.CallToolResult(
            content=[
                mcp.types.ImageContent(type="image", data="abc", mimeType="image/png")
            ],
            isError=True,
        )

        with pytest.raises(ToolError, match="Tool 'my_tool' returned an error"):
            await _parse_call_tool_result(
                name="my_tool",
                result=result,
                tool_output_schemas={},
                list_tools_fn=None,
                raise_on_error=True,
            )

    async def test_error_with_text_content_raises_with_message(self):
        result = mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="custom error msg")],
            isError=True,
        )

        with pytest.raises(ToolError, match="custom error msg"):
            await _parse_call_tool_result(
                name="my_tool",
                result=result,
                tool_output_schemas={},
                list_tools_fn=None,
                raise_on_error=True,
            )

    async def test_error_with_structured_content_does_not_parse_data(self):
        result = mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="error happened")],
            isError=True,
            structuredContent={"key": "value"},
        )

        parsed = await _parse_call_tool_result(
            name="my_tool",
            result=result,
            tool_output_schemas={},
            list_tools_fn=None,
            raise_on_error=False,
        )

        assert parsed.is_error is True
        assert parsed.data is None
        assert parsed.structured_content == {"key": "value"}


class TestLogLevel:
    async def test_tool_error_with_custom_log_level(self, caplog):
        """ToolError with custom log_level should log at specified level."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def custom_level_tool():
            raise ToolError("Missing required parameter", log_level=logging.WARNING)

        async with Client(transport=FastMCPTransport(mcp)) as client:
            with caplog.at_level(logging.WARNING):
                result = await client.call_tool_mcp("custom_level_tool", {})

        assert result.isError
        assert isinstance(result.content[0], TextContent)
        assert "Missing required parameter" in result.content[0].text
        assert any(
            "Error calling tool" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )
        assert not any(
            "Error calling tool" in record.message and record.levelname == "ERROR"
            for record in caplog.records
        )

    async def test_regular_tool_error_logs_at_error(self, caplog):
        """ToolError with default log_level logs at ERROR."""
        mcp = FastMCP("TestServer")

        @mcp.tool
        def regular_error_tool():
            raise ToolError("Something went wrong")

        async with Client(transport=FastMCPTransport(mcp)) as client:
            with caplog.at_level(logging.ERROR):
                result = await client.call_tool_mcp("regular_error_tool", {})

        assert result.isError
        assert isinstance(result.content[0], TextContent)
        assert "Something went wrong" in result.content[0].text
        assert any(
            "Error calling tool 'regular_error_tool'" in record.message
            and record.levelname == "ERROR"
            for record in caplog.records
        )

    async def test_resource_error_with_custom_log_level(self, caplog):
        """ResourceError with custom log_level should log at specified level."""
        mcp = FastMCP("TestServer")

        @mcp.resource("test://custom")
        def custom_level_resource():
            raise ResourceError(
                "Resource unavailable, try again later", log_level=logging.WARNING
            )

        async with Client(transport=FastMCPTransport(mcp)) as client:
            with caplog.at_level(logging.WARNING):
                with pytest.raises(Exception) as exc_info:
                    await client.read_resource_mcp("test://custom")

        assert "Resource unavailable, try again later" in str(exc_info.value)
        assert any(
            "Error reading resource" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )
        assert not any(
            "Error reading resource" in record.message and record.levelname == "ERROR"
            for record in caplog.records
        )

    async def test_regular_resource_error_logs_at_error(self, caplog):
        """ResourceError with default log_level logs at ERROR."""
        mcp = FastMCP("TestServer")

        @mcp.resource("test://regular")
        def regular_resource():
            raise ResourceError("Something went wrong")

        async with Client(transport=FastMCPTransport(mcp)) as client:
            with caplog.at_level(logging.ERROR):
                with pytest.raises(Exception) as exc_info:
                    await client.read_resource_mcp("test://regular")

        assert "Something went wrong" in str(exc_info.value)
        assert any(
            "Error reading resource 'test://regular'" in record.message
            and record.levelname == "ERROR"
            for record in caplog.records
        )

    async def test_prompt_error_with_custom_log_level(self, caplog):
        """PromptError with custom log_level should log at specified level."""
        mcp = FastMCP("TestServer")

        @mcp.prompt
        def custom_level_prompt():
            raise PromptError(
                "Insufficient context, provide more details", log_level=logging.WARNING
            )

        async with Client(transport=FastMCPTransport(mcp)) as client:
            with caplog.at_level(logging.WARNING):
                with pytest.raises(Exception) as exc_info:
                    await client.get_prompt("custom_level_prompt")

        assert "Insufficient context" in str(exc_info.value)
        assert any(
            "Error rendering prompt" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )
        assert not any(
            "Error rendering prompt" in record.message and record.levelname == "ERROR"
            for record in caplog.records
        )

    async def test_regular_prompt_error_logs_at_error(self, caplog):
        """PromptError with default log_level logs at ERROR."""
        mcp = FastMCP("TestServer")

        @mcp.prompt
        def regular_prompt():
            raise PromptError("Something went wrong")

        async with Client(transport=FastMCPTransport(mcp)) as client:
            with caplog.at_level(logging.ERROR):
                with pytest.raises(Exception) as exc_info:
                    await client.get_prompt("regular_prompt")

        assert "Something went wrong" in str(exc_info.value)
        assert any(
            "Error rendering prompt 'regular_prompt'" in record.message
            and record.levelname == "ERROR"
            for record in caplog.records
        )

    async def test_sampling_tool_error_with_custom_log_level(self, caplog):
        """ToolError with custom log_level in sampling should log at specified level."""
        from mcp.types import ToolUseContent

        from fastmcp.server.sampling.run import SamplingTool, execute_tools

        async def custom_level_sampling_tool(x: int) -> int:
            raise ToolError("Expected sampling error", log_level=logging.WARNING)

        tool = SamplingTool.from_function(custom_level_sampling_tool)
        tool_use = ToolUseContent(
            type="tool_use",
            id="test-id",
            name="custom_level_sampling_tool",
            input={"x": 42},
        )

        with caplog.at_level(logging.WARNING):
            results = await execute_tools(
                tool_calls=[tool_use],
                tool_map={"custom_level_sampling_tool": tool},
                mask_error_details=False,
            )

        assert len(results) == 1
        assert results[0].isError
        assert "Expected sampling error" in results[0].content[0].text  # type: ignore
        assert any(
            "Error calling sampling tool" in record.message
            and record.levelname == "WARNING"
            for record in caplog.records
        )
        assert not any(
            "Error calling sampling tool" in record.message
            and record.levelname == "ERROR"
            for record in caplog.records
        )
