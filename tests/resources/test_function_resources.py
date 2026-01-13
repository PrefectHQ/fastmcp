import pytest
from pydantic import AnyUrl, BaseModel

from fastmcp.resources.function_resource import FunctionResource
from fastmcp.resources.resource import ResourceContent


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
        # read() returns raw value
        result = await resource.read()
        assert result == "Hello, world!"

        # _read() converts to ResourceResult
        result = await resource._read()
        assert len(result.contents) == 1
        assert result.contents[0].content == "Hello, world!"
        assert result.contents[0].mime_type == "text/plain"

    async def test_read_binary(self):
        """Test reading binary data from a FunctionResource."""

        def get_data() -> bytes:
            return b"Hello, world!"

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        # read() returns raw value
        result = await resource.read()
        assert result == b"Hello, world!"

        # _read() converts to ResourceResult
        result = await resource._read()
        assert result.contents[0].content == b"Hello, world!"

    async def test_dict_return_raises_type_error(self):
        """Returning dict from read() raises TypeError - use ResourceResult."""

        def get_data() -> dict:
            return {"key": "value"}

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        # read() returns raw value (no type checking at runtime)
        result = await resource.read()
        assert result == {"key": "value"}

        # _read() raises TypeError - must return str, bytes, or ResourceResult
        with pytest.raises(TypeError, match="must be str, bytes, or list"):
            await resource._read()

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

    async def test_basemodel_return_raises_type_error(self):
        """Returning BaseModel from read() raises TypeError - use ResourceResult."""

        class MyModel(BaseModel):
            name: str

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=lambda: MyModel(name="test"),
        )
        # read() returns raw value (no type checking at runtime)
        raw_result = await resource.read()
        assert isinstance(raw_result, MyModel)

        # _read() raises TypeError - must return str, bytes, or ResourceResult
        with pytest.raises(TypeError, match="must be str, bytes, or list"):
            await resource._read()

    async def test_custom_type_return_raises_type_error(self):
        """Returning custom type from read() raises TypeError - use ResourceResult."""

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
        # read() returns raw value (no type checking at runtime)
        raw_result = await resource.read()
        assert isinstance(raw_result, CustomData)

        # _read() raises TypeError - must return str, bytes, or ResourceResult
        with pytest.raises(TypeError, match="must be str, bytes, or list"):
            await resource._read()

    async def test_async_read_text(self):
        """Test reading text from async FunctionResource."""

        async def get_data() -> str:
            return "Hello, world!"

        resource = FunctionResource(
            uri=AnyUrl("function://test"),
            name="test",
            fn=get_data,
        )
        # read() returns raw value
        result = await resource.read()
        assert result == "Hello, world!"

        # _read() converts to ResourceResult
        result = await resource._read()
        assert result.contents[0].content == "Hello, world!"
        assert result.contents[0].mime_type == "text/plain"

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
        """Test that ResourceContent auto-sets mime_type defaults."""
        content = ResourceContent(content="plain text")
        assert content.content == "plain text"
        assert content.mime_type == "text/plain"  # Auto-set for string content
        assert content.meta is None

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


class TestFunctionResourceCallable:
    """Test FunctionResource with callable objects."""

    async def test_callable_object_sync(self):
        """Test that callable objects with sync __call__ work."""

        class MyResource:
            def __init__(self, value: str):
                self.value = value

            def __call__(self) -> str:
                return f"value: {self.value}"

        resource = FunctionResource.from_function(MyResource("test"), uri="fn://test")
        result = await resource.read()
        assert result == "value: test"

    async def test_callable_object_async(self):
        """Test that callable objects with async __call__ work."""

        class AsyncResource:
            def __init__(self, value: str):
                self.value = value

            async def __call__(self) -> str:
                return f"async value: {self.value}"

        resource = FunctionResource.from_function(
            AsyncResource("test"), uri="fn://test"
        )
        result = await resource.read()
        assert result == "async value: test"

    async def test_sync_resource_runs_concurrently(self):
        """Test that sync resources run in threadpool and don't block each other."""
        import asyncio
        import threading

        num_calls = 3
        barrier = threading.Barrier(num_calls, timeout=0.5)

        def concurrent_resource() -> str:
            barrier.wait()
            return "done"

        resource = FunctionResource.from_function(concurrent_resource, uri="fn://test")

        # Run concurrent reads - will raise BrokenBarrierError if not concurrent
        results = await asyncio.gather(
            resource.read(),
            resource.read(),
            resource.read(),
        )
        assert results == ["done", "done", "done"]
