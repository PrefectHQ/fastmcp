import pytest

from fastmcp.resources import ResourceTemplate


class TestQueryParameterExtraction:
    """Test basic query parameter extraction from URIs."""

    async def test_single_query_param(self):
        """Test resource template with single query parameter."""

        def get_data(id: str, format: str = "json") -> str:
            return f"Data {id} in {format}"

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="data://{id}{?format}",
            name="test",
        )

        # Match without query param (uses default)
        params = template.matches("data://123")
        assert params == {"id": "123"}

        # Match with query param
        params = template.matches("data://123?format=xml")
        assert params == {"id": "123", "format": "xml"}

    async def test_multiple_query_params(self):
        """Test resource template with multiple query parameters."""

        def get_items(category: str, page: int = 1, limit: int = 10) -> str:
            return f"Category {category}, page {page}, limit {limit}"

        template = ResourceTemplate.from_function(
            fn=get_items,
            uri_template="items://{category}{?page,limit}",
            name="test",
        )

        # No query params
        params = template.matches("items://books")
        assert params == {"category": "books"}

        # One query param
        params = template.matches("items://books?page=2")
        assert params == {"category": "books", "page": "2"}

        # Both query params
        params = template.matches("items://books?page=2&limit=20")
        assert params == {"category": "books", "page": "2", "limit": "20"}


class TestQueryParameterTypeCoercion:
    """Test type coercion for query parameters."""

    async def test_int_coercion(self):
        """Test integer type coercion for query parameters."""

        def get_page(resource: str, page: int = 1) -> dict:
            return {"resource": resource, "page": page, "type": type(page).__name__}

        template = ResourceTemplate.from_function(
            fn=get_page,
            uri_template="resource://{resource}{?page}",
            name="test",
        )

        # Create resource with string query param
        resource = await template.create_resource(
            "resource://docs?page=5",
            {"resource": "docs", "page": "5"},
        )

        # read() returns raw dict
        result = await resource.read()
        assert isinstance(result, dict)
        assert result["page"] == 5
        assert result["type"] == "int"

    async def test_bool_coercion(self):
        """Test boolean type coercion for query parameters."""

        def get_config(name: str, enabled: bool = False) -> dict:
            return {"name": name, "enabled": enabled, "type": type(enabled).__name__}

        template = ResourceTemplate.from_function(
            fn=get_config,
            uri_template="config://{name}{?enabled}",
            name="test",
        )

        # Test true value
        resource = await template.create_resource(
            "config://feature?enabled=true",
            {"name": "feature", "enabled": "true"},
        )
        # read() returns raw dict
        result = await resource.read()
        assert isinstance(result, dict)
        assert result["enabled"] is True

        # Test false value
        resource = await template.create_resource(
            "config://feature?enabled=false",
            {"name": "feature", "enabled": "false"},
        )
        result = await resource.read()
        assert isinstance(result, dict)
        assert result["enabled"] is False

    async def test_float_coercion(self):
        """Test float type coercion for query parameters."""

        def get_metrics(service: str, threshold: float = 0.5) -> dict:
            return {
                "service": service,
                "threshold": threshold,
                "type": type(threshold).__name__,
            }

        template = ResourceTemplate.from_function(
            fn=get_metrics,
            uri_template="metrics://{service}{?threshold}",
            name="test",
        )

        resource = await template.create_resource(
            "metrics://api?threshold=0.95",
            {"service": "api", "threshold": "0.95"},
        )

        # read() returns raw dict
        result = await resource.read()
        assert isinstance(result, dict)
        assert result["threshold"] == 0.95
        assert result["type"] == "float"


class TestQueryParameterValidation:
    """Test validation rules for query parameters."""

    def test_query_params_must_be_optional(self):
        """Test that query parameters must have default values."""

        def invalid_func(id: str, format: str) -> str:
            return f"Data {id} in {format}"

        with pytest.raises(
            ValueError,
            match="Query parameters .* must be optional function parameters with default values",
        ):
            ResourceTemplate.from_function(
                fn=invalid_func,
                uri_template="data://{id}{?format}",
                name="test",
            )

    def test_required_params_in_path(self):
        """Test that required parameters must be in path."""

        def valid_func(id: str, format: str = "json") -> str:
            return f"Data {id} in {format}"

        # This should work - required param in path, optional in query
        template = ResourceTemplate.from_function(
            fn=valid_func,
            uri_template="data://{id}{?format}",
            name="test",
        )
        assert template.uri_template == "data://{id}{?format}"


class TestQueryParameterWithDefaults:
    """Test that missing query parameters use default values."""

    async def test_missing_query_param_uses_default(self):
        """Test that missing query parameters fall back to defaults."""

        def get_data(id: str, format: str = "json", verbose: bool = False) -> dict:
            return {"id": id, "format": format, "verbose": verbose}

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="data://{id}{?format,verbose}",
            name="test",
        )

        # No query params - should use defaults
        resource = await template.create_resource(
            "data://123",
            {"id": "123"},
        )

        # read() returns raw dict
        result = await resource.read()
        assert isinstance(result, dict)
        assert result["format"] == "json"
        assert result["verbose"] is False

    async def test_partial_query_params(self):
        """Test providing only some query parameters."""

        def get_data(
            id: str, format: str = "json", limit: int = 10, offset: int = 0
        ) -> dict:
            return {"id": id, "format": format, "limit": limit, "offset": offset}

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="data://{id}{?format,limit,offset}",
            name="test",
        )

        # Provide only some query params
        resource = await template.create_resource(
            "data://123?limit=20",
            {"id": "123", "limit": "20"},
        )

        # read() returns raw dict
        result = await resource.read()
        assert isinstance(result, dict)
        assert result["format"] == "json"  # default
        assert result["limit"] == 20  # provided
        assert result["offset"] == 0  # default


class TestQueryParameterWithWildcards:
    """Test query parameters combined with wildcard path parameters."""

    async def test_wildcard_with_query_params(self):
        """Test combining wildcard path params with query params."""

        def get_file(path: str, encoding: str = "utf-8", lines: int = 100) -> dict:
            return {"path": path, "encoding": encoding, "lines": lines}

        template = ResourceTemplate.from_function(
            fn=get_file,
            uri_template="files://{path*}{?encoding,lines}",
            name="test",
        )

        # Match path with query params
        params = template.matches("files://src/test/data.txt?encoding=ascii&lines=50")
        assert params == {
            "path": "src/test/data.txt",
            "encoding": "ascii",
            "lines": "50",
        }

        # Create resource
        resource = await template.create_resource(
            "files://src/test/data.txt?lines=50",
            {"path": "src/test/data.txt", "lines": "50"},
        )

        # read() returns raw dict
        result = await resource.read()
        assert isinstance(result, dict)
        assert result["path"] == "src/test/data.txt"
        assert result["encoding"] == "utf-8"  # default
        assert result["lines"] == 50  # provided


class TestResourceTemplateFieldDefaults:
    """Test resource templates with Field() defaults."""

    async def test_field_with_default(self):
        """Test that Field(default=...) correctly provides default values in resource templates."""
        from pydantic import Field

        def get_data(
            id: str = Field(description="Resource ID"),
            format: str = Field(default="json", description="Output format"),
        ) -> str:
            return f"id={id}, format={format}"

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="data://{id}{?format}",
            name="test",
        )

        # Test with only required parameter
        resource = await template.create_resource("data://123", {"id": "123"})
        result = await resource.read()
        assert result == "id=123, format=json"

        # Test with override
        resource = await template.create_resource(
            "data://123?format=xml", {"id": "123", "format": "xml"}
        )
        result = await resource.read()
        assert result == "id=123, format=xml"

    async def test_multiple_field_defaults(self):
        """Test multiple query parameters with Field() defaults."""
        from typing import Any

        from pydantic import Field

        def fetch_data(
            resource_id: str = Field(description="Resource ID"),
            limit: int = Field(default=10, description="Result limit"),
            offset: int = Field(default=0, description="Result offset"),
            format: str = Field(default="json", description="Output format"),
        ) -> dict[str, Any]:
            return {
                "resource_id": resource_id,
                "limit": limit,
                "offset": offset,
                "format": format,
            }

        template = ResourceTemplate.from_function(
            fn=fetch_data,
            uri_template="api://{resource_id}{?limit,offset,format}",
            name="test",
        )

        # Test with only required parameter - all defaults should apply
        resource1 = await template.create_resource(
            "api://user123", {"resource_id": "user123"}
        )
        result1 = await resource1.read()
        assert isinstance(result1, dict)
        assert result1["resource_id"] == "user123"
        assert result1["limit"] == 10
        assert result1["offset"] == 0
        assert result1["format"] == "json"

        # Test with some overrides
        resource2 = await template.create_resource(
            "api://user123?limit=50&format=xml",
            {"resource_id": "user123", "limit": "50", "format": "xml"},
        )
        result2 = await resource2.read()
        assert isinstance(result2, dict)
        assert result2["resource_id"] == "user123"
        assert result2["limit"] == 50  # overridden
        assert result2["offset"] == 0  # default
        assert result2["format"] == "xml"  # overridden
