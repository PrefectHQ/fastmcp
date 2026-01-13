import functools
from urllib.parse import quote

import pytest
from pydantic import BaseModel

from fastmcp import Context, FastMCP
from fastmcp.resources import ResourceTemplate
from fastmcp.resources.function_resource import FunctionResource
from fastmcp.resources.template import match_uri_template


class TestResourceTemplate:
    """Test ResourceTemplate functionality."""

    def test_template_creation(self):
        """Test creating a template from a function."""

        def my_func(key: str, value: int) -> dict:
            return {"key": key, "value": value}

        template = ResourceTemplate.from_function(
            fn=my_func,
            uri_template="test://{key}/{value}",
            name="test",
        )
        assert template.uri_template == "test://{key}/{value}"
        assert template.name == "test"
        assert template.mime_type == "text/plain"  # default

        assert template.fn(key="test", value=42) == my_func(key="test", value=42)

    def test_template_matches(self):
        """Test matching URIs against a template."""

        def my_func(key: str, value: int) -> dict:
            return {"key": key, "value": value}

        template = ResourceTemplate.from_function(
            fn=my_func,
            uri_template="test://{key}/{value}",
            name="test",
        )

        # Valid match
        params = template.matches("test://foo/123")
        assert params == {"key": "foo", "value": "123"}

        # No match
        assert template.matches("test://foo") is None
        assert template.matches("other://foo/123") is None

    def test_template_matches_with_prefix(self):
        """Test matching URIs against a template with a prefix."""

        def my_func(key: str, value: int) -> dict:
            return {"key": key, "value": value}

        template = ResourceTemplate.from_function(
            fn=my_func,
            uri_template="app+test://{key}/{value}",
            name="test",
        )

        # Valid match
        params = template.matches("app+test://foo/123")
        assert params == {"key": "foo", "value": "123"}

        # No match
        assert template.matches("test://foo/123") is None
        assert template.matches("test://foo") is None
        assert template.matches("other://foo/123") is None

    def test_template_uri_validation(self):
        """Test validation rule: URI template must have at least one parameter."""

        def my_func() -> dict:
            return {"data": "value"}

        with pytest.raises(
            ValueError, match="URI template must contain at least one parameter"
        ):
            ResourceTemplate.from_function(
                fn=my_func,
                uri_template="test://no-params",
                name="test",
            )

    def test_template_uri_params_subset_of_function_params(self):
        """Test validation rule: URI parameters must be a subset of function parameters."""

        def my_func(key: str, value: int) -> dict:
            return {"key": key, "value": value}

        # This should work - URI params are a subset of function params
        template = ResourceTemplate.from_function(
            fn=my_func,
            uri_template="test://{key}/{value}",
            name="test",
        )
        assert template.uri_template == "test://{key}/{value}"

        # This should fail - 'unknown' is not a function parameter
        with pytest.raises(
            ValueError,
            match="Required function arguments .* must be a subset of the URI path parameters",
        ):
            ResourceTemplate.from_function(
                fn=my_func,
                uri_template="test://{key}/{unknown}",
                name="test",
            )

    def test_required_params_subset_of_uri_params(self):
        """Test validation rule: Required function parameters must be in URI parameters."""

        # Function with required parameters
        def func_with_required(
            required_param: str, optional_param: str = "default"
        ) -> dict:
            return {"required": required_param, "optional": optional_param}

        # This should work - required param is in URI
        template = ResourceTemplate.from_function(
            fn=func_with_required,
            uri_template="test://{required_param}",
            name="test",
        )
        assert template.uri_template == "test://{required_param}"

        # This should fail - required param is not in URI
        with pytest.raises(
            ValueError,
            match="Required function arguments .* must be a subset of the URI path parameters",
        ):
            ResourceTemplate.from_function(
                fn=func_with_required,
                uri_template="test://{optional_param}",
                name="test",
            )

    def test_multiple_required_params(self):
        """Test validation with multiple required parameters."""

        def multi_required(param1: str, param2: int, optional: str = "default") -> dict:
            return {"p1": param1, "p2": param2, "opt": optional}

        # This works - all required params in URI
        template = ResourceTemplate.from_function(
            fn=multi_required,
            uri_template="test://{param1}/{param2}",
            name="test",
        )
        assert template.uri_template == "test://{param1}/{param2}"

        # This fails - missing one required param
        with pytest.raises(
            ValueError,
            match="Required function arguments .* must be a subset of the URI path parameters",
        ):
            ResourceTemplate.from_function(
                fn=multi_required,
                uri_template="test://{param1}",
                name="test",
            )

    async def test_create_resource(self):
        """Test creating a resource from a template."""

        def my_func(key: str, value: int) -> str:
            return f"key={key}, value={value}"

        template = ResourceTemplate.from_function(
            fn=my_func,
            uri_template="test://{key}/{value}",
            name="test",
        )

        resource = await template.create_resource(
            "test://foo/123",
            {"key": "foo", "value": 123},
        )

        assert isinstance(resource, FunctionResource)
        # read() returns raw value from function
        result = await resource.read()
        assert result == "key=foo, value=123"

        # _read() wraps in ResourceResult
        resource_result = await resource._read()
        assert len(resource_result.contents) == 1
        assert resource_result.contents[0].content == "key=foo, value=123"

    async def test_async_text_resource(self):
        """Test creating a text resource from async function."""

        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        template = ResourceTemplate.from_function(
            fn=greet,
            uri_template="greet://{name}",
            name="greeter",
        )

        resource = await template.create_resource(
            "greet://world",
            {"name": "world"},
        )

        assert isinstance(resource, FunctionResource)
        # read() returns raw value
        result = await resource.read()
        assert result == "Hello, world!"

    async def test_async_binary_resource(self):
        """Test creating a binary resource from async function."""

        async def get_bytes(value: str) -> bytes:
            return value.encode()

        template = ResourceTemplate.from_function(
            fn=get_bytes,
            uri_template="bytes://{value}",
            name="bytes",
        )

        resource = await template.create_resource(
            "bytes://test",
            {"value": "test"},
        )

        assert isinstance(resource, FunctionResource)
        # read() returns raw bytes
        result = await resource.read()
        assert result == b"test"

    async def test_basemodel_conversion(self):
        """Test handling of BaseModel types."""

        class MyModel(BaseModel):
            key: str
            value: int

        def get_data(key: str, value: int) -> MyModel:
            return MyModel(key=key, value=value)

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="test://{key}/{value}",
            name="test",
        )

        resource = await template.create_resource(
            "test://foo/123",
            {"key": "foo", "value": 123},
        )

        assert isinstance(resource, FunctionResource)
        # read() returns raw BaseModel
        result = await resource.read()
        assert isinstance(result, MyModel)
        data = result.model_dump()
        assert data == {"key": "foo", "value": 123}

    async def test_custom_type_conversion(self):
        """Test handling of custom types."""

        class CustomData:
            def __init__(self, value: str):
                self.value = value

            def __str__(self) -> str:
                return self.value

        def get_data(value: str) -> CustomData:
            return CustomData(value)

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="test://{value}",
            name="test",
        )

        resource = await template.create_resource(
            "test://hello",
            {"value": "hello"},
        )

        assert isinstance(resource, FunctionResource)
        # read() returns raw CustomData object
        result = await resource.read()
        assert isinstance(result, CustomData)
        assert str(result) == "hello"

    async def test_wildcard_param_can_create_resource(self):
        """Test that wildcard parameters are valid."""

        def identity(path: str) -> str:
            return path

        template = ResourceTemplate.from_function(
            fn=identity,
            uri_template="test://{path*}.py",
            name="test",
        )

        assert await template.create_resource(
            "test://path/to/test.py",
            {"path": "path/to/test.py"},
        )

    async def test_wildcard_param_matches(self):
        def identify(path: str) -> str:
            return path

        template = ResourceTemplate.from_function(
            fn=identify,
            uri_template="test://src/{path*}.py",
            name="test",
        )
        # Valid match
        params = template.matches("test://src/path/to/test.py")
        assert params == {"path": "path/to/test"}

    async def test_multiple_wildcard_params(self):
        """Test that multiple wildcard parameters are valid."""

        def identity(path: str, path2: str) -> str:
            return f"{path}/{path2}"

        template = ResourceTemplate.from_function(
            fn=identity,
            uri_template="test://{path*}/xyz/{path2*}",
            name="test",
        )

        params = template.matches("test://path/to/xyz/abc")
        assert params == {"path": "path/to", "path2": "abc"}

    async def test_wildcard_param_with_regular_param(self):
        """Test that a wildcard parameter can be used with a regular parameter."""

        def identity(prefix: str, path: str) -> str:
            return f"{prefix}/{path}"

        template = ResourceTemplate.from_function(
            fn=identity,
            uri_template="test://{prefix}/{path*}",
            name="test",
        )

        params = template.matches("test://src/path/to/test.py")
        assert params == {"prefix": "src", "path": "path/to/test.py"}

    async def test_function_with_varargs_not_allowed(self):
        def func(x: int, *args: int) -> int:
            return x + sum(args)

        with pytest.raises(
            ValueError,
            match=r"Functions with \*args are not supported as resource templates",
        ):
            ResourceTemplate.from_function(
                fn=func,
                uri_template="test://{x}/{args*}",
                name="test",
            )

    async def test_function_with_varkwargs_ok(self):
        def func(x: int, **kwargs: int) -> int:
            return x + sum(kwargs.values())

        template = ResourceTemplate.from_function(
            fn=func,
            uri_template="test://{x}/{y}/{z}",
            name="test",
        )
        assert template.uri_template == "test://{x}/{y}/{z}"

    async def test_callable_object_as_template(self):
        """Test that a callable object can be used as a template."""

        class MyTemplate:
            """This is my template"""

            def __call__(self, x: str) -> str:
                """ignore this"""
                return f"X was {x}"

        template = ResourceTemplate.from_function(
            fn=MyTemplate(),
            uri_template="test://{x}",
            name="test",
        )

        resource = await template.create_resource(
            "test://foo",
            {"x": "foo"},
        )

        assert isinstance(resource, FunctionResource)
        # read() returns raw string from __call__
        result = await resource.read()
        assert result == "X was foo"


class TestMatchUriTemplate:
    """Test match_uri_template function."""

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("test://a/b", None),
            ("test://a/b/c", None),
            ("test://a/x/b", {"x": "x"}),
            ("test://a/x/y/b", None),
            ("test://a/1-2/b", {"x": "1-2"}),
        ],
    )
    def test_match_uri_template_single_param(
        self, uri: str, expected_params: dict[str, str]
    ):
        """Test that match_uri_template uses the slash delimiter."""
        uri_template = "test://a/{x}/b"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("test://foo/123", {"x": "foo", "y": "123"}),
            ("test://bar/456", {"x": "bar", "y": "456"}),
            ("test://foo/bar", {"x": "foo", "y": "bar"}),
            ("test://foo/bar/baz", None),
            ("test://foo/email@domain.com", {"x": "foo", "y": "email@domain.com"}),
            ("test://two words/foo", {"x": "two words", "y": "foo"}),
            ("test://two.words/foo+bar", {"x": "two.words", "y": "foo+bar"}),
            (
                f"test://escaped{quote('/', safe='')}word/bar",
                {"x": "escaped/word", "y": "bar"},
            ),
            (
                f"test://escaped{quote('{', safe='')}x{quote('}', safe='')}word/bar",
                {"x": "escaped{x}word", "y": "bar"},
            ),
            ("prefix+test://foo/123", None),
            ("test://foo", None),
            ("other://foo/123", None),
            ("t.est://foo/bar", None),
        ],
    )
    def test_match_uri_template_simple_params(
        self, uri: str, expected_params: dict[str, str] | None
    ):
        """Test matching URIs against a template with simple parameters."""
        uri_template = "test://{x}/{y}"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("test://a/b/foo/c/d/123", {"x": "foo", "y": "123"}),
            ("test://a/b/bar/c/d/456", {"x": "bar", "y": "456"}),
            ("prefix+test://a/b/foo/c/d/123", None),
            ("test://a/b/foo", None),
            ("other://a/b/foo/c/d/123", None),
        ],
    )
    def test_match_uri_template_params_and_literal_segments(
        self, uri: str, expected_params: dict[str, str] | None
    ):
        """Test matching URIs against a template with parameters and literal segments."""
        uri_template = "test://a/b/{x}/c/d/{y}"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("prefix+test://foo/test/123", {"x": "foo", "y": "123"}),
            ("prefix+test://bar/test/456", {"x": "bar", "y": "456"}),
            ("test://foo/test/123", None),
            ("other.prefix+test://foo/test/123", None),
            ("other+prefix+test://foo/test/123", None),
        ],
    )
    def test_match_uri_template_with_prefix(
        self, uri: str, expected_params: dict[str, str] | None
    ):
        """Test matching URIs against a template with a prefix."""
        uri_template = "prefix+test://{x}/test/{y}"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params

    def test_match_uri_template_quoted_params(self):
        uri_template = "user://{name}/{email}"
        quoted_name = quote("John Doe", safe="")
        quoted_email = quote("john@example.com", safe="")
        uri = f"user://{quoted_name}/{quoted_email}"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == {"name": "John Doe", "email": "john@example.com"}

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("test://a/b", None),
            ("test://a/b/c", None),
            ("test://a/x/b", {"x": "x"}),
            ("test://a/x/y/b", {"x": "x/y"}),
            ("bad-prefix://a/x/y/b", None),
            ("test://a/x/y/z", None),
        ],
    )
    def test_match_uri_template_wildcard_param(
        self, uri: str, expected_params: dict[str, str]
    ):
        """Test that match_uri_template uses the slash delimiter."""
        uri_template = "test://a/{x*}/b"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("test://a/x/y/b/c/d", {"x": "x/y", "y": "c/d"}),
            ("bad-prefix://a/x/y/b/c/d", None),
            ("test://a/x/y/c/d", None),
            ("test://a/x/b/y", {"x": "x", "y": "y"}),
        ],
    )
    def test_match_uri_template_multiple_wildcard_params(
        self, uri: str, expected_params: dict[str, str]
    ):
        """Test that match_uri_template uses the slash delimiter."""
        uri_template = "test://a/{x*}/b/{y*}"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params

    def test_match_uri_template_wildcard_and_literal_param(self):
        """Test that match_uri_template uses the slash delimiter."""
        uri = "test://a/x/y/b"
        uri_template = "test://a/{x*}/{y}"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == {"x": "x/y", "y": "b"}

    def test_match_consecutive_params(self):
        """Test that consecutive parameters without a / are not matched."""
        uri = "test://a/x/y"
        uri_template = "test://a/{x}{y}"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result is None

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("file://abc/xyz.py", {"path": "xyz"}),
            ("file://abc/x/y/z.py", {"path": "x/y/z"}),
            ("file://abc/x/y/z/.py", {"path": "x/y/z/"}),
            ("file://abc/x/y/z.md", None),
            ("file://x/y/z.txt", None),
        ],
    )
    def test_match_uri_template_with_non_slash_suffix(
        self, uri: str, expected_params: dict[str, str]
    ):
        uri_template = "file://abc/{path*}.py"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("resource://test_foo", {"x": "foo"}),
            ("resource://test_bar", {"x": "bar"}),
            ("resource://test_hello", {"x": "hello"}),
            ("resource://test_with_underscores", {"x": "with_underscores"}),
            ("resource://test_", None),  # Empty parameter not matched
            ("resource://test", None),  # Missing parameter delimiter
            ("resource://other_foo", None),  # Wrong prefix
            ("other://test_foo", None),  # Wrong scheme
        ],
    )
    def test_match_uri_template_embedded_param(
        self, uri: str, expected_params: dict[str, str] | None
    ):
        """Test matching URIs where parameter is embedded within a word segment."""
        uri_template = "resource://test_{x}"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("resource://prefix_foo_suffix", {"x": "foo"}),
            ("resource://prefix_bar_suffix", {"x": "bar"}),
            ("resource://prefix_hello_world_suffix", {"x": "hello_world"}),
            ("resource://prefix__suffix", None),  # Empty parameter not matched
            ("resource://prefix_suffix", None),  # Missing parameter delimiter
            ("resource://other_foo_suffix", None),  # Wrong prefix
            ("resource://prefix_foo_other", None),  # Wrong suffix
        ],
    )
    def test_match_uri_template_embedded_param_with_prefix_and_suffix(
        self, uri: str, expected_params: dict[str, str] | None
    ):
        """Test matching URIs where parameter has both prefix and suffix."""
        uri_template = "resource://prefix_{x}_suffix"
        result = match_uri_template(uri=uri, uri_template=uri_template)
        assert result == expected_params


class TestContextHandling:
    """Test context handling in resource templates."""

    def test_context_parameter_detection(self):
        """Test that context parameters are properly detected in
        ResourceTemplate.from_function()."""

        def template_with_context(x: int, ctx: Context) -> str:
            return str(x)

        ResourceTemplate.from_function(
            fn=template_with_context,
            uri_template="test://{x}",
            name="test",
        )

        def template_without_context(x: int) -> str:
            return str(x)

        ResourceTemplate.from_function(
            fn=template_without_context,
            uri_template="test://{x}",
            name="test",
        )

    def test_parameterized_context_parameter_detection(self):
        """Test that parameterized context parameters are properly detected in
        ResourceTemplate.from_function()."""

        def template_with_context(x: int, ctx: Context) -> str:
            return str(x)

        ResourceTemplate.from_function(
            fn=template_with_context,
            uri_template="test://{x}",
            name="test",
        )

    def test_parameterized_union_context_parameter_detection(self):
        """Test that context parameters in a union are properly detected in
        ResourceTemplate.from_function()."""

        def template_with_context(x: int, ctx: Context | None) -> str:
            return str(x)

        ResourceTemplate.from_function(
            fn=template_with_context,
            uri_template="test://{x}",
            name="test",
        )

    async def test_context_injection(self):
        """Test that context is properly injected during resource creation."""

        def resource_with_context(x: int, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return str(x)

        template = ResourceTemplate.from_function(
            fn=resource_with_context,
            uri_template="test://{x}",
            name="test",
        )

        from fastmcp import FastMCP

        mcp = FastMCP()
        context = Context(fastmcp=mcp)

        async with context:
            resource = await template.create_resource(
                "test://42",
                {"x": 42},
            )

            assert isinstance(resource, FunctionResource)
            # read() returns the raw value
            result = await resource.read()
            assert result == "42"

    async def test_context_optional(self):
        """Test that context is optional when creating resources."""

        def resource_with_context(x: int, ctx: Context | None = None) -> str:
            return str(x)

        template = ResourceTemplate.from_function(
            fn=resource_with_context,
            uri_template="test://{x}",
            name="test",
        )

        # Even for optional context, we need to provide a context
        mcp = FastMCP()
        context = Context(fastmcp=mcp)

        async with context:
            resource = await template.create_resource(
                "test://42",
                {"x": 42},
            )

            assert isinstance(resource, FunctionResource)
            # read() returns the raw value
            result = await resource.read()
            assert result == "42"

    async def test_context_with_functools_wraps_decorator(self):
        """Regression test for #2524: decorated templates with Context should work."""

        def custom_decorator(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            return wrapper

        @custom_decorator
        async def decorated_template(ctx: Context, item_id: int) -> str:
            assert isinstance(ctx, Context)
            return f"item: {item_id}"

        template = ResourceTemplate.from_function(
            fn=decorated_template,
            uri_template="test://{item_id}",
            name="test",
        )

        mcp = FastMCP()
        context = Context(fastmcp=mcp)

        async with context:
            resource = await template.create_resource("test://42", {"item_id": 42})
            # read() returns the raw value
            result = await resource.read()
            assert result == "item: 42"


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
