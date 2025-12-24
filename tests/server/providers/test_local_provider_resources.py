"""Tests for resource and template behavior in LocalProvider.

Tests cover:
- Resource context injection
- Resource templates and URI parsing
- Resource template context injection
- Resource decorator patterns
- Template decorator patterns
"""

import pytest
from mcp import McpError
from mcp.types import BlobResourceContents, TextResourceContents
from pydantic import AnyUrl

from fastmcp import Client, Context, FastMCP
from fastmcp.resources import Resource, ResourceContent, ResourceTemplate


class TestResourceContext:
    async def test_resource_with_context_annotation_gets_context(self):
        mcp = FastMCP()

        @mcp.resource("resource://test")
        def resource_with_context(ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return ctx.request_id

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "1"


class TestResourceTemplates:
    async def test_resource_with_params_not_in_uri(self):
        """Test that a resource with function parameters raises an error if the URI
        parameters don't match"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="URI template must contain at least one parameter",
        ):

            @mcp.resource("resource://data")
            def get_data_fn(param: str) -> str:
                return f"Data: {param}"

    async def test_resource_with_uri_params_without_args(self):
        """Test that a resource with URI parameters is automatically a template"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="URI parameters .* must be a subset of the function arguments",
        ):

            @mcp.resource("resource://{param}")
            def get_data() -> str:
                return "Data"

    async def test_resource_with_untyped_params(self):
        """Test that a resource with untyped parameters raises an error"""
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def get_data(param) -> str:
            return "Data"

    async def test_resource_matching_params(self):
        """Test that a resource with matching URI and function parameters works"""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Data for test"

    async def test_resource_mismatched_params(self):
        """Test that mismatched parameters raise an error"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="Required function arguments .* must be a subset of the URI path parameters",
        ):

            @mcp.resource("resource://{name}/data")
            def get_data(user: str) -> str:
                return f"Data for {user}"

    async def test_resource_multiple_params(self):
        """Test that multiple parameters work correctly"""
        mcp = FastMCP()

        @mcp.resource("resource://{org}/{repo}/data")
        def get_data(org: str, repo: str) -> str:
            return f"Data for {org}/{repo}"

        async with Client(mcp) as client:
            result = await client.read_resource(
                AnyUrl("resource://cursor/fastmcp/data")
            )
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Data for cursor/fastmcp"

    async def test_resource_multiple_mismatched_params(self):
        """Test that mismatched parameters raise an error"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="Required function arguments .* must be a subset of the URI path parameters",
        ):

            @mcp.resource("resource://{org}/{repo}/data")
            def get_data_mismatched(org: str, repo_2: str) -> str:
                return f"Data for {org}"

    async def test_template_with_varkwargs(self):
        """Test that a template can have **kwargs."""
        mcp = FastMCP()

        @mcp.resource("test://{x}/{y}/{z}")
        def func(**kwargs: int) -> int:
            return sum(kwargs.values())

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("test://1/2/3"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "6"

    async def test_template_with_default_params(self):
        """Test that a template can have default parameters."""
        mcp = FastMCP()

        @mcp.resource("math://add/{x}")
        def add(x: int, y: int = 10) -> int:
            return x + y

        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].uri_template == "math://add/{x}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("math://add/5"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "15"

            result2 = await client.read_resource(AnyUrl("math://add/7"))
            assert isinstance(result2[0], TextResourceContents)
            assert result2[0].text == "17"

    async def test_template_to_resource_conversion(self):
        """Test that a template can be converted to a resource."""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].uri_template == "resource://{name}/data"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Data for test"

    async def test_template_decorator_with_tags(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}", tags={"template", "test-tag"})
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}"]
        assert template.tags == {"template", "test-tag"}

    async def test_template_decorator_wildcard_param(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param*}")
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Template resource: test/data"

    async def test_template_with_query_params(self):
        """Test RFC 6570 query parameters in resource templates."""
        mcp = FastMCP()

        @mcp.resource("data://{id}{?format,limit}")
        def get_data(id: str, format: str = "json", limit: int = 10) -> str:
            return f"id={id}, format={format}, limit={limit}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("data://123"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "id=123, format=json, limit=10"

            result = await client.read_resource(AnyUrl("data://123?format=xml"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "id=123, format=xml, limit=10"

            result = await client.read_resource(
                AnyUrl("data://123?format=csv&limit=50")
            )
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "id=123, format=csv, limit=50"

    async def test_templates_match_in_order_of_definition(self):
        """If a wildcard template is defined first, it will take priority."""
        mcp = FastMCP()

        @mcp.resource("resource://{param*}")
        def template_resource(param: str) -> str:
            return f"Template resource 1: {param}"

        @mcp.resource("resource://{x}/{y}")
        def template_resource_with_params(x: str, y: str) -> str:
            return f"Template resource 2: {x}/{y}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://a/b/c"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Template resource 1: a/b/c"

            result = await client.read_resource(AnyUrl("resource://a/b"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Template resource 1: a/b"

    async def test_templates_shadow_each_other_reorder(self):
        """If a wildcard template is defined second, it will *not* take priority."""
        mcp = FastMCP()

        @mcp.resource("resource://{x}/{y}")
        def template_resource_with_params(x: str, y: str) -> str:
            return f"Template resource 1: {x}/{y}"

        @mcp.resource("resource://{param*}")
        def template_resource(param: str) -> str:
            return f"Template resource 2: {param}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://a/b/c"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Template resource 2: a/b/c"

            result = await client.read_resource(AnyUrl("resource://a/b"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Template resource 1: a/b"

    async def test_resource_template_with_annotations(self):
        """Test that resource template annotations are visible to clients."""
        mcp = FastMCP()

        @mcp.resource(
            "api://users/{user_id}",
            annotations={"httpMethod": "GET", "Cache-Control": "no-cache"},
        )
        def get_user(user_id: str) -> str:
            return f"User {user_id} data"

        async with Client(mcp) as client:
            templates = await client.list_resource_templates()
            assert len(templates) == 1

            template = templates[0]
            assert template.uriTemplate == "api://users/{user_id}"

            assert template.annotations is not None
            assert hasattr(template.annotations, "httpMethod")
            assert getattr(template.annotations, "httpMethod") == "GET"
            assert hasattr(template.annotations, "Cache-Control")
            assert getattr(template.annotations, "Cache-Control") == "no-cache"


class TestResourceTemplateContext:
    async def test_resource_template_context(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def resource_template(param: str, ctx: Context) -> str:
            assert isinstance(ctx, Context)
            return f"Resource template: {param} {ctx.request_id}"

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text.startswith("Resource template: test 1")

    async def test_resource_template_context_with_callable_object(self):
        mcp = FastMCP()

        class MyResource:
            def __call__(self, param: str, ctx: Context) -> str:
                return f"Resource template: {param} {ctx.request_id}"

        template = ResourceTemplate.from_function(
            MyResource(), uri_template="resource://{param}"
        )
        mcp.add_template(template)

        async with Client(mcp) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text.startswith("Resource template: test 1")


class TestResourceDecorator:
    async def test_no_resources_before_decorator(self):
        mcp = FastMCP()

        with pytest.raises(McpError, match="Unknown resource"):
            async with Client(mcp) as client:
                await client.read_resource("resource://data")

    async def test_resource_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        def get_data() -> str:
            return "Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Hello, world!"

    async def test_resource_decorator_incorrect_usage(self):
        mcp = FastMCP()

        with pytest.raises(
            TypeError, match="The @resource decorator was used incorrectly"
        ):

            @mcp.resource  # Missing parentheses #type: ignore
            def get_data() -> str:
                return "Hello, world!"

    async def test_resource_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", name="custom-data")
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].name == "custom-data"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Hello, world!"

    async def test_resource_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.resource("resource://data", description="Data resource")
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].description == "Data resource"

    async def test_resource_decorator_with_tags(self):
        """Test that the resource decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.resource("resource://data", tags={"example", "test-tag"})
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resources = list(resources_dict.values())
        assert len(resources) == 1
        assert resources[0].tags == {"example", "test-tag"}

    async def test_resource_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, prefix: str):
                self.prefix = prefix

            def get_data(self) -> str:
                return f"{self.prefix} Hello, world!"

        obj = MyClass("My prefix:")

        mcp.add_resource(
            Resource.from_function(
                obj.get_data, uri="resource://data", name="instance-resource"
            )
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "My prefix: Hello, world!"

    async def test_resource_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def get_data(cls) -> str:
                return f"{cls.prefix} Hello, world!"

        mcp.add_resource(
            Resource.from_function(
                MyClass.get_data, uri="resource://data", name="class-resource"
            )
        )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Class prefix: Hello, world!"

    async def test_resource_decorator_classmethod_error(self):
        mcp = FastMCP()

        with pytest.raises(ValueError, match="To decorate a classmethod"):

            class MyClass:
                @mcp.resource("resource://data")
                @classmethod
                def get_data(cls) -> None:
                    pass

    async def test_resource_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.resource("resource://data")
            @staticmethod
            def get_data() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Static Hello, world!"

    async def test_resource_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.resource("resource://data")
        async def get_data() -> str:
            return "Async Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Async Hello, world!"

    async def test_resource_decorator_staticmethod_order(self):
        """Test that both decorator orders work for static methods"""
        mcp = FastMCP()

        class MyClass:
            @mcp.resource("resource://data")  # type: ignore[misc]
            @staticmethod
            def get_data() -> str:
                return "Static Hello, world!"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Static Hello, world!"

    async def test_resource_decorator_with_meta(self):
        """Test that meta parameter is passed through the resource decorator."""
        mcp = FastMCP()

        meta_data = {"version": "1.0", "author": "test"}

        @mcp.resource("resource://data", meta=meta_data)
        def get_data() -> str:
            return "Hello, world!"

        resources_dict = await mcp.get_resources()
        resource = resources_dict["resource://data"]

        assert resource.meta == meta_data

    async def test_resource_content_with_meta_in_response(self):
        """Test that ResourceContent meta is passed through to MCP response."""
        mcp = FastMCP()

        @mcp.resource("resource://widget")
        def get_widget() -> ResourceContent:
            return ResourceContent(
                content="<widget>content</widget>",
                mime_type="text/html",
                meta={"csp": "script-src 'self'", "version": "1.0"},
            )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://widget")
            assert len(result) == 1
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "<widget>content</widget>"
            assert result[0].mimeType == "text/html"
            assert isinstance(result[0], TextResourceContents)
            assert result[0].meta == {"csp": "script-src 'self'", "version": "1.0"}

    async def test_resource_content_binary_with_meta(self):
        """Test that ResourceContent with binary content and meta works."""
        mcp = FastMCP()

        @mcp.resource("resource://binary")
        def get_binary() -> ResourceContent:
            return ResourceContent(
                content=b"\x00\x01\x02",
                meta={"encoding": "raw"},
            )

        async with Client(mcp) as client:
            result = await client.read_resource("resource://binary")
            assert len(result) == 1
            assert hasattr(result[0], "blob")
            assert isinstance(result[0], BlobResourceContents)
            assert result[0].meta == {"encoding": "raw"}

    async def test_resource_content_without_meta(self):
        """Test that ResourceContent without meta works (meta is None)."""
        mcp = FastMCP()

        @mcp.resource("resource://plain")
        def get_plain() -> ResourceContent:
            return ResourceContent(content="plain content")

        async with Client(mcp) as client:
            result = await client.read_resource("resource://plain")
            assert len(result) == 1
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "plain content"
            assert isinstance(result[0], TextResourceContents)
            assert result[0].meta is None


class TestTemplateDecorator:
    async def test_template_decorator(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].name == "get_data"
        assert templates[0].uri_template == "resource://{name}/data"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Data for test"

    async def test_template_decorator_incorrect_usage(self):
        mcp = FastMCP()

        with pytest.raises(
            TypeError, match="The @resource decorator was used incorrectly"
        ):

            @mcp.resource  # Missing parentheses #type: ignore
            def get_data(name: str) -> str:
                return f"Data for {name}"

    async def test_template_decorator_with_name(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data", name="custom-template")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].name == "custom-template"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
        assert isinstance(result[0], TextResourceContents)
        assert result[0].text == "Data for test"

    async def test_template_decorator_with_description(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data", description="Template description")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        templates_dict = await mcp.get_resource_templates()
        templates = list(templates_dict.values())
        assert len(templates) == 1
        assert templates[0].description == "Template description"

    async def test_template_decorator_instance_method(self):
        mcp = FastMCP()

        class MyClass:
            def __init__(self, prefix: str):
                self.prefix = prefix

            def get_data(self, name: str) -> str:
                return f"{self.prefix} Data for {name}"

        obj = MyClass("My prefix:")
        template = ResourceTemplate.from_function(
            obj.get_data,
            uri_template="resource://{name}/data",
            name="instance-template",
        )
        mcp.add_template(template)

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "My prefix: Data for test"

    async def test_template_decorator_classmethod(self):
        mcp = FastMCP()

        class MyClass:
            prefix = "Class prefix:"

            @classmethod
            def get_data(cls, name: str) -> str:
                return f"{cls.prefix} Data for {name}"

        template = ResourceTemplate.from_function(
            MyClass.get_data,
            uri_template="resource://{name}/data",
            name="class-template",
        )
        mcp.add_template(template)

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Class prefix: Data for test"

    async def test_template_decorator_staticmethod(self):
        mcp = FastMCP()

        class MyClass:
            @mcp.resource("resource://{name}/data")
            @staticmethod
            def get_data(name: str) -> str:
                return f"Static Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Static Data for test"

    async def test_template_decorator_async_function(self):
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        async def get_data(name: str) -> str:
            return f"Async Data for {name}"

        async with Client(mcp) as client:
            result = await client.read_resource("resource://test/data")
            assert isinstance(result[0], TextResourceContents)
            assert result[0].text == "Async Data for test"

    async def test_template_decorator_with_tags(self):
        """Test that the template decorator properly sets tags."""
        mcp = FastMCP()

        @mcp.resource("resource://{param}", tags={"template", "test-tag"})
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}"]
        assert template.tags == {"template", "test-tag"}

    async def test_template_decorator_wildcard_param(self):
        mcp = FastMCP()

        @mcp.resource("resource://{param*}")
        def template_resource(param: str) -> str:
            return f"Template resource: {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param*}"]
        assert template.uri_template == "resource://{param*}"
        assert template.name == "template_resource"

    async def test_template_decorator_with_meta(self):
        """Test that meta parameter is passed through the template decorator."""
        mcp = FastMCP()

        meta_data = {"version": "2.0", "template": "test"}

        @mcp.resource("resource://{param}/data", meta=meta_data)
        def get_template_data(param: str) -> str:
            return f"Data for {param}"

        templates_dict = await mcp.get_resource_templates()
        template = templates_dict["resource://{param}/data"]

        assert template.meta == meta_data
