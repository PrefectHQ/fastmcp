import mcp.types
import pytest
from pydantic import AnyUrl, BaseModel

from fastmcp import Client, FastMCP
from fastmcp.resources import Resource, ResourceContent, ResourceResult
from fastmcp.resources.function_resource import FunctionResource


class TestResourceValidation:
    """Test base Resource validation."""

    def test_resource_uri_validation(self):
        """Test URI validation."""

        def dummy_func() -> str:
            return "data"

        # Valid URI
        resource = FunctionResource(
            uri=AnyUrl("http://example.com/data"),
            name="test",
            fn=dummy_func,
        )
        assert str(resource.uri) == "http://example.com/data"

        # Missing protocol
        with pytest.raises(ValueError, match="Input should be a valid URL"):
            FunctionResource(
                uri=AnyUrl("invalid"),
                name="test",
                fn=dummy_func,
            )

        # Missing host
        with pytest.raises(ValueError, match="Input should be a valid URL"):
            FunctionResource(
                uri=AnyUrl("http://"),
                name="test",
                fn=dummy_func,
            )

    def test_resource_name_from_uri(self):
        """Test name is extracted from URI if not provided."""

        def dummy_func() -> str:
            return "data"

        resource = FunctionResource(
            uri=AnyUrl("resource://my-resource"),
            fn=dummy_func,
        )
        assert resource.name == "resource://my-resource"

    def test_provided_name_takes_precedence_over_uri(self):
        """Test that provided name takes precedence over URI."""

        def dummy_func() -> str:
            return "data"

        # Explicit name takes precedence over URI
        resource = FunctionResource(
            uri=AnyUrl("resource://uri-name"),
            name="explicit-name",
            fn=dummy_func,
        )
        assert resource.name == "explicit-name"

    def test_resource_mime_type(self):
        """Test mime type handling."""

        def dummy_func() -> str:
            return "data"

        # Default mime type
        resource = FunctionResource(
            uri=AnyUrl("resource://test"),
            fn=dummy_func,
        )
        assert resource.mime_type == "text/plain"

        # Custom mime type
        resource = FunctionResource(
            uri=AnyUrl("resource://test"),
            fn=dummy_func,
            mime_type="application/json",
        )
        assert resource.mime_type == "application/json"

    async def test_resource_read_not_implemented(self):
        """Test that Resource.read() raises NotImplementedError."""

        class ConcreteResource(Resource):
            pass

        resource = ConcreteResource(uri=AnyUrl("test://test"), name="test")
        with pytest.raises(NotImplementedError, match="Subclasses must implement read"):
            await resource.read()

    def test_resource_meta_parameter(self):
        """Test that meta parameter is properly handled."""

        def resource_func() -> str:
            return "test content"

        meta_data = {"version": "1.0", "category": "test"}
        resource = Resource.from_function(
            fn=resource_func,
            uri="resource://test",
            name="test_resource",
            meta=meta_data,
        )

        assert resource.meta == meta_data
        mcp_resource = resource.to_mcp_resource()
        # MCP resource includes fastmcp meta, so check that our meta is included
        assert mcp_resource.meta is not None
        assert meta_data.items() <= mcp_resource.meta.items()


class TestResourceContent:
    """Test ResourceContent creation and conversion."""

    def test_string_content(self):
        """String input creates text content with text/plain mime type."""
        content = ResourceContent("hello world")
        assert content.content == "hello world"
        assert content.mime_type == "text/plain"
        assert content.meta is None

    def test_bytes_content(self):
        """Bytes input creates binary content with octet-stream mime type."""
        content = ResourceContent(b"\x00\x01\x02")
        assert content.content == b"\x00\x01\x02"
        assert content.mime_type == "application/octet-stream"
        assert content.meta is None

    def test_dict_serialized_to_json(self):
        """Dict input is JSON-serialized with application/json mime type."""
        content = ResourceContent({"key": "value", "count": 42})
        assert content.content == '{"key":"value","count":42}'
        assert content.mime_type == "application/json"

    def test_list_serialized_to_json(self):
        """List input is JSON-serialized."""
        content = ResourceContent([1, 2, 3])
        assert content.content == "[1,2,3]"
        assert content.mime_type == "application/json"

    def test_pydantic_model_serialized_to_json(self):
        """Pydantic model is JSON-serialized."""

        class Item(BaseModel):
            name: str
            price: float

        content = ResourceContent(Item(name="Widget", price=9.99))
        assert content.content == '{"name":"Widget","price":9.99}'
        assert content.mime_type == "application/json"

    def test_custom_mime_type(self):
        """Custom mime type overrides default."""
        content = ResourceContent("test", mime_type="text/html")
        assert content.mime_type == "text/html"

    def test_with_meta(self):
        """Meta is passed through to content."""
        content = ResourceContent("test", meta={"version": "1.0"})
        assert content.meta == {"version": "1.0"}

    def test_to_mcp_text_contents(self):
        """Text content converts to TextResourceContents."""
        content = ResourceContent(
            content="hello", mime_type="text/plain", meta={"k": "v"}
        )
        mcp_content = content.to_mcp_resource_contents("resource://test")
        assert isinstance(mcp_content, mcp.types.TextResourceContents)
        assert mcp_content.text == "hello"
        assert mcp_content.mimeType == "text/plain"
        assert str(mcp_content.uri) == "resource://test"
        assert mcp_content.meta == {"k": "v"}

    def test_to_mcp_blob_contents(self):
        """Binary content converts to BlobResourceContents with base64."""
        content = ResourceContent(
            content=b"\x00\x01\x02", mime_type="application/octet-stream"
        )
        mcp_content = content.to_mcp_resource_contents("resource://binary")
        assert isinstance(mcp_content, mcp.types.BlobResourceContents)
        assert mcp_content.blob == "AAEC"  # base64 of \x00\x01\x02
        assert mcp_content.mimeType == "application/octet-stream"


class TestResourceResult:
    """Test ResourceResult initialization and conversion."""

    def test_init_from_string(self):
        """String input is normalized to list[ResourceContent]."""
        result = ResourceResult("hello world")
        assert len(result.contents) == 1
        assert result.contents[0].content == "hello world"
        assert result.contents[0].mime_type == "text/plain"

    def test_init_from_bytes(self):
        """Bytes input is normalized to list[ResourceContent]."""
        result = ResourceResult(b"\xff\xfe")
        assert len(result.contents) == 1
        assert result.contents[0].content == b"\xff\xfe"
        assert result.contents[0].mime_type == "application/octet-stream"

    def test_init_from_dict_raises_type_error(self):
        """Dict input raises TypeError - must use ResourceContent for serialization."""
        with pytest.raises(TypeError, match="must be str, bytes, or list"):
            ResourceResult({"page": 1, "total": 100})  # type: ignore[arg-type]

    def test_init_from_single_resource_content_raises_type_error(self):
        """Single ResourceContent raises TypeError - must be in a list."""
        content = ResourceContent(content="test", mime_type="text/html")
        with pytest.raises(TypeError, match="must be str, bytes, or list"):
            ResourceResult(content)  # type: ignore[arg-type]

    def test_init_from_list_of_resource_content(self):
        """List of ResourceContent is used directly."""
        contents = [
            ResourceContent(content="one", mime_type="text/plain"),
            ResourceContent(content="two", mime_type="text/plain"),
        ]
        result = ResourceResult(contents)
        assert len(result.contents) == 2
        assert result.contents[0].content == "one"
        assert result.contents[1].content == "two"

    def test_init_from_mixed_list_raises_type_error(self):
        """Mixed list items raise TypeError - all items must be ResourceContent."""
        with pytest.raises(TypeError, match=r"contents\[0\] must be ResourceContent"):
            ResourceResult(["text", b"bytes", {"key": "value"}])  # type: ignore[arg-type]

    def test_init_preserves_meta(self):
        """Meta is preserved on ResourceResult."""
        result = ResourceResult("test", meta={"version": "2.0"})
        assert result.meta == {"version": "2.0"}

    def test_to_mcp_result(self):
        """Converts to MCP ReadResourceResult with proper structure."""
        result = ResourceResult(
            contents=[ResourceContent(content="hello", mime_type="text/plain")],
            meta={"source": "test"},
        )
        mcp_result = result.to_mcp_result("resource://test")
        assert isinstance(mcp_result, mcp.types.ReadResourceResult)
        assert len(mcp_result.contents) == 1
        assert isinstance(mcp_result.contents[0], mcp.types.TextResourceContents)
        assert mcp_result.contents[0].text == "hello"
        assert str(mcp_result.contents[0].uri) == "resource://test"
        assert mcp_result.meta == {"source": "test"}

    def test_to_mcp_result_multiple_contents(self):
        """Multiple contents all get same URI."""
        result = ResourceResult(
            [
                ResourceContent("one"),
                ResourceContent("two"),
                ResourceContent("three"),
            ]
        )
        mcp_result = result.to_mcp_result("resource://multi")
        assert len(mcp_result.contents) == 3
        for item in mcp_result.contents:
            assert str(item.uri) == "resource://multi"


class TestResourceConvertResult:
    """Test Resource.convert_result() method."""

    def test_passthrough_resource_result(self):
        """ResourceResult input is returned unchanged."""

        def fn() -> str:
            return "test"

        resource = FunctionResource(uri=AnyUrl("test://test"), name="test", fn=fn)
        original = ResourceResult("test", meta={"original": True})
        result = resource.convert_result(original)
        assert result is original

    def test_converts_raw_value(self):
        """Raw values are converted to ResourceResult."""

        def fn() -> str:
            return "test"

        resource = FunctionResource(uri=AnyUrl("test://test"), name="test", fn=fn)
        result = resource.convert_result("hello")
        assert isinstance(result, ResourceResult)
        assert len(result.contents) == 1
        assert result.contents[0].content == "hello"

    async def test_read_returns_resource_result(self):
        """_read() returns ResourceResult after conversion."""

        def fn() -> str:
            return "hello world"

        resource = FunctionResource(uri=AnyUrl("test://test"), name="test", fn=fn)
        result = await resource._read()
        assert len(result.contents) == 1
        assert result.contents[0].content == "hello world"


class TestResourceMetaPropagation:
    """Test that meta is properly propagated through the full MCP flow."""

    async def test_resource_result_meta_received_by_client(self):
        """Meta set on ResourceResult is received by MCP client."""
        mcp = FastMCP()

        @mcp.resource("test://with-meta")
        def resource_with_meta() -> ResourceResult:
            return ResourceResult("hello", meta={"version": "2.0", "source": "test"})

        async with Client(mcp) as client:
            result = await client.read_resource_mcp("test://with-meta")
            assert result.meta == {"version": "2.0", "source": "test"}

    async def test_resource_content_meta_received_by_client(self):
        """Meta set on ResourceContent is received by MCP client."""
        mcp = FastMCP()

        @mcp.resource("test://content-meta")
        def resource_with_content_meta() -> ResourceResult:
            return ResourceResult(
                [ResourceContent(content="data", meta={"item_version": "1.0"})]
            )

        async with Client(mcp) as client:
            result = await client.read_resource_mcp("test://content-meta")
            assert len(result.contents) == 1
            assert result.contents[0].meta == {"item_version": "1.0"}

    async def test_both_result_and_content_meta(self):
        """Both result-level and content-level meta are propagated."""
        mcp = FastMCP()

        @mcp.resource("test://both-meta")
        def resource_both_meta() -> ResourceResult:
            return ResourceResult(
                contents=[
                    ResourceContent(content="item", meta={"item_key": "item_val"})
                ],
                meta={"result_key": "result_val"},
            )

        async with Client(mcp) as client:
            result = await client.read_resource_mcp("test://both-meta")
            assert result.meta == {"result_key": "result_val"}
            assert result.contents[0].meta == {"item_key": "item_val"}
