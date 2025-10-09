import pytest
from pydantic import AnyUrl

from fastmcp.resources import Resource
from fastmcp.resources.resource import FunctionResource


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

        resource = ConcreteResource(uri=AnyUrl("test://test"), name="test")  # type: ignore
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
