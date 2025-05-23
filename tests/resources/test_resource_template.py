import json
from urllib.parse import quote

import pytest
from pydantic import BaseModel

from fastmcp import Context
from fastmcp.resources import FunctionResource, ResourceTemplate
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
        test_input = {"key": "test", "value": 42}
        assert template.fn(**test_input) == my_func(**test_input)

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
            match="Required function arguments .* must be a subset of the URI parameters",
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
            match="Required function arguments .* must be a subset of the URI parameters",
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
            match="Required function arguments .* must be a subset of the URI parameters",
        ):
            ResourceTemplate.from_function(
                fn=multi_required,
                uri_template="test://{param1}",
                name="test",
            )

    async def test_create_resource(self):
        """Test creating a resource from a template."""

        def my_func(key: str, value: int) -> dict:
            return {"key": key, "value": value}

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
        content = await resource.read()
        assert isinstance(content, str)
        data = json.loads(content)
        assert data == {"key": "foo", "value": 123}

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
        content = await resource.read()
        assert content == "Hello, world!"

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
        content = await resource.read()
        assert content == b"test"

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
        content = await resource.read()
        assert isinstance(content, str)
        data = json.loads(content)
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
        content = await resource.read()
        assert content == '"hello"'

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
        content = await resource.read()
        assert content == "X was foo"


class TestMatchUriTemplate:
    """Test match_uri_template function."""

    @pytest.mark.parametrize(
        "uri, expected_params",
        [
            ("test://a/b", None),
            ("test://a/b/c", None),
            ("test://a/x/b", {"x": "x"}),
            ("test://a/x/y/b", None),
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

        with context:
            resource = await template.create_resource(
                "test://42",
                {"x": 42},
            )

        assert isinstance(resource, FunctionResource)
        content = await resource.read()
        assert content == "42"

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
        from fastmcp import FastMCP

        mcp = FastMCP()
        context = Context(fastmcp=mcp)

        with context:
            resource = await template.create_resource(
                "test://42",
                {"x": 42},
            )

        assert isinstance(resource, FunctionResource)
        content = await resource.read()
        assert content == "42"
