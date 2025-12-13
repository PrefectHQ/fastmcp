import pytest
from pydantic import AnyUrl, BaseModel

from fastmcp.resources.resource import FunctionResource, ResourceContent


class TestFunctionResource:
    """Test FunctionResource functionality."""

    def test_function_resource_creation(self):
        """Test creating a FunctionResource."""

        def my_func() -> str:
            return "test content"

        resource = FunctionResource(
            uri=AnyUrl("fn://test"),
            name="test",
            description="test function",
            fn=my_func,
        )
        assert str(resource.uri) == "fn://test"
        assert resource.name == "test"
        assert resource.description == "test function"
        assert resource.mime_type == "text/plain"  # default
        assert resource.fn == my_func

    async def test_read_text(self):
        """Test reading text from a FunctionResource."""

        def get_data() -> str:
            return "Hello, world!"

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert result.content == "Hello, world!"
        assert result.mime_type == "text/plain"

    async def test_read_binary(self):
        """Test reading binary data from a FunctionResource."""

        def get_data() -> bytes:
            return b"Hello, world!"

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert result.content == b"Hello, world!"

    async def test_json_conversion(self):
        """Test automatic JSON conversion of non-string results."""

        def get_data() -> dict:
            return {"key": "value"}

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert isinstance(result.content, str)
        assert '"key":"value"' in result.content

    async def test_error_handling(self):
        """Test error handling in FunctionResource."""

        def failing_func() -> str:
            raise ValueError("Test error")

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=failing_func,
        )
        with pytest.raises(ValueError, match="Test error"):
            await resource.read()

    async def test_basemodel_conversion(self):
        """Test handling of BaseModel types."""

        class MyModel(BaseModel):
            name: str

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=lambda: MyModel(name="test"),
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert result.content == '{"name":"test"}'

    async def test_custom_type_conversion(self):
        """Test handling of custom types."""

        class CustomData:
            def __str__(self) -> str:
                return "custom data"

        def get_data() -> CustomData:
            return CustomData()

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert isinstance(result.content, str)

    async def test_async_read_text(self):
        """Test reading text from async FunctionResource."""

        async def get_data() -> str:
            return "Hello, world!"

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert result.content == "Hello, world!"
        assert result.mime_type == "text/plain"

    async def test_resource_content_text(self):
        """Test returning ResourceContent with text content."""

        def get_data() -> ResourceContent:
            return ResourceContent(
                content="Hello, world!",
                mime_type="text/html",
                meta={"csp": "script-src 'self'"},
            )

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert result.content == "Hello, world!"
        assert result.mime_type == "text/html"
        assert result.meta == {"csp": "script-src 'self'"}

    async def test_resource_content_binary(self):
        """Test returning ResourceContent with binary content."""

        def get_data() -> ResourceContent:
            return ResourceContent(
                content=b"\x00\x01\x02",
                mime_type="application/octet-stream",
            )

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert result.content == b"\x00\x01\x02"
        assert result.mime_type == "application/octet-stream"
        assert result.meta is None

    async def test_resource_content_without_meta(self):
        """Test returning ResourceContent without meta."""

        def get_data() -> ResourceContent:
            return ResourceContent(content="plain text")

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert result.content == "plain text"
        assert result.mime_type is None
        assert result.meta is None

    async def test_async_resource_content(self):
        """Test async function returning ResourceContent."""

        async def get_data() -> ResourceContent:
            return ResourceContent(
                content="async content",
                meta={"key": "value"},
            )

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        result = await resource.read()
        assert isinstance(result, ResourceContent)
        assert result.content == "async content"
        assert result.meta == {"key": "value"}


class TestResourceContentToMcp:
    """Test ResourceContent.to_mcp_resource_contents method."""

    def test_text_content_to_mcp(self):
        """Test converting text ResourceContent to MCP type."""
        rc = ResourceContent(
            content="hello world",
            mime_type="text/html",
            meta={"csp": "script-src 'self'"},
        )
        mcp_content = rc.to_mcp_resource_contents("resource://test")

        assert hasattr(mcp_content, "text")
        assert mcp_content.text == "hello world"
        assert mcp_content.mimeType == "text/html"
        assert mcp_content.meta == {"csp": "script-src 'self'"}

    def test_binary_content_to_mcp(self):
        """Test converting binary ResourceContent to MCP type."""
        rc = ResourceContent(
            content=b"\x00\x01\x02",
            mime_type="application/octet-stream",
            meta={"encoding": "raw"},
        )
        mcp_content = rc.to_mcp_resource_contents("resource://test")

        assert hasattr(mcp_content, "blob")
        assert mcp_content.blob == "AAEC"  # base64 of \x00\x01\x02
        assert mcp_content.mimeType == "application/octet-stream"
        assert mcp_content.meta == {"encoding": "raw"}

    def test_default_mime_types(self):
        """Test default mime types are applied correctly."""
        text_rc = ResourceContent(content="text")
        text_mcp = text_rc.to_mcp_resource_contents("resource://test")
        assert text_mcp.mimeType == "text/plain"

        binary_rc = ResourceContent(content=b"binary")
        binary_mcp = binary_rc.to_mcp_resource_contents("resource://test")
        assert binary_mcp.mimeType == "application/octet-stream"

    def test_none_meta(self):
        """Test that None meta is handled correctly."""
        rc = ResourceContent(content="no meta")
        mcp_content = rc.to_mcp_resource_contents("resource://test")

        assert mcp_content.meta is None
