"""Tests for RFC 6570 level-3 operator support in resource templates."""

import pytest
from mcp.types import TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.resources import ResourceTemplate
from fastmcp.resources.template import (
    expand_uri_template,
    extract_exploded_query_params,
    extract_query_params,
)
from fastmcp.server import create_proxy


class TestQueryParamExtraction:
    """Test extract_query_params handles all query-relevant level-3 operators."""

    def test_extract_initial_query_params(self):
        """Test extraction from {?param1,param2} syntax."""
        params = extract_query_params("data://items{?page,size}")
        assert params == {"page", "size"}

    def test_extract_continuation_query_params(self):
        """Test extraction from {&param1,param2} syntax."""
        params = extract_query_params("data://items{?page}{&size}")
        assert params == {"page", "size"}

    def test_extract_continuation_only(self):
        """Test extraction from {&param} when no {?...} is present."""
        params = extract_query_params("data://items{&page,size}")
        assert params == {"page", "size"}

    def test_path_style_params_are_not_query_params(self):
        """{;param} expands inside the path, so it is not a query param."""
        params = extract_query_params("data://items{;page,size}")
        assert params == set()

    def test_extract_every_continuation_group(self):
        """Every {&...} group contributes, not just the first."""
        params = extract_query_params("data://items{?filter}{&page}{&sort}")
        assert params == {"filter", "page", "sort"}

    def test_exploded_continuation_param(self):
        """{&tags*} marks tags as exploded, like {?tags*}."""
        assert extract_exploded_query_params("data://items{?q}{&tags*}") == {"tags"}

    def test_strip_explode_modifier(self):
        """Test that the explode modifier (*) is stripped."""
        params = extract_query_params("data://items{?tags*}{&filters*}")
        assert params == {"tags", "filters"}

    def test_comma_separated_in_continuation(self):
        """Test that {&a,b} splits into multiple params."""
        params = extract_query_params("data://items{&page,size,limit}")
        assert params == {"page", "size", "limit"}


class TestIssue5706Repro:
    """Test the exact reproduction case from issue #5706."""

    async def test_continuation_param_list_and_read(self):
        """Test that {&size} continuation param is listed and readable."""

        def items(page: int = 1, size: int = 10) -> str:
            return f"page={page} size={size}"

        template = ResourceTemplate.from_function(
            fn=items,
            uri_template="data://items{?page}{&size}",
            name="items",
        )

        # Test all three URIs from the issue
        # 1. Both query params present
        params = template.matches("data://items?page=2&size=5")
        assert params is not None, "URI with both params should match"
        assert params == {"page": "2", "size": "5"}

        resource = await template.create_resource("data://items?page=2&size=5", params)
        result = await resource.read()
        assert result == "page=2 size=5"

        # 2. Only initial query param present
        params = template.matches("data://items?page=2")
        assert params is not None, "URI with only ?page should match"
        assert params == {"page": "2"}

        resource = await template.create_resource("data://items?page=2", params)
        result = await resource.read()
        assert result == "page=2 size=10"  # size uses default

        # 3. Bare URI (no query params)
        params = template.matches("data://items")
        assert params is not None, "Bare URI should match"
        assert params == {}

        resource = await template.create_resource("data://items", params)
        result = await resource.read()
        assert result == "page=1 size=10"  # both use defaults


class TestIssue5706OverClient:
    """The #5706 repro read through a Client, directly and through forwarding."""

    @pytest.mark.parametrize("via", ["direct", "mount", "proxy"])
    @pytest.mark.parametrize(
        "query, expected",
        [
            ("?page=2&size=5", "page=2 size=5"),
            ("?page=2", "page=2 size=10"),
            ("", "page=1 size=10"),
        ],
    )
    async def test_list_and_read(self, via: str, query: str, expected: str):
        child = FastMCP("child")

        @child.resource("data://items{?page}{&size}")
        def items(page: int = 1, size: int = 10) -> str:
            return f"page={page} size={size}"

        uri = f"data://items{query}"
        if via == "direct":
            server = child
        elif via == "mount":
            server = FastMCP("parent")
            server.mount(child, namespace="ns")
            uri = f"data://ns/items{query}"
        else:
            server = create_proxy(child)

        async with Client(server) as client:
            templates = await client.list_resource_templates()
            assert [t.uri_template for t in templates] == [
                uri.split("?")[0] + "{?page}{&size}"
            ]
            [contents] = await client.read_resource(uri)
            assert isinstance(contents, TextResourceContents)
            assert contents.text == expected

    async def test_second_continuation_group_and_exploded(self):
        mcp = FastMCP()

        @mcp.resource("x://m{?a}{&b}{&c}")
        def m(a: str = "", b: str = "", c: str = "") -> str:
            return f"a={a} b={b} c={c}"

        @mcp.resource("x://u{?q}{&tags*}")
        def u(q: str = "", tags: list[str] | None = None) -> str:
            return f"q={q} tags={tags}"

        async with Client(mcp) as client:
            [m_contents] = await client.read_resource("x://m?a=1&b=2&c=3")
            [u_contents] = await client.read_resource("x://u?q=1&tags=a&tags=b")
        assert isinstance(m_contents, TextResourceContents)
        assert isinstance(u_contents, TextResourceContents)
        assert m_contents.text == "a=1 b=2 c=3"
        assert u_contents.text == "q=1 tags=['a', 'b']"


class TestExpandContinuation:
    def test_expand_continuation_after_query(self):
        uri = expand_uri_template("data://items{?page}{&size}", {"page": 2, "size": 5})
        assert uri == "data://items?page=2&size=5"

    def test_expand_continuation_without_query_value(self):
        uri = expand_uri_template("data://items{?page}{&size}", {"size": 5})
        assert uri == "data://items?size=5"


class TestUnsupportedLevel3Operators:
    """Test that unsupported level-3 operators are rejected at registration."""

    def test_reject_reserved_expansion(self):
        """Test that {+path} reserved expansion is rejected."""

        def get_file(path: str) -> str:
            return f"File: {path}"

        with pytest.raises(
            ValueError,
            match=r"RFC 6570 level-3 operators \{\+\.\.\.\} are not supported",
        ):
            ResourceTemplate.from_function(
                fn=get_file,
                uri_template="files://{+path}",
                name="file",
            )

    def test_reject_fragment(self):
        """Test that {#fragment} is rejected."""

        def get_section(doc: str, section: str) -> str:
            return f"Doc: {doc}, Section: {section}"

        with pytest.raises(
            ValueError,
            match=r"RFC 6570 level-3 operators \{#\.\.\.\} are not supported",
        ):
            ResourceTemplate.from_function(
                fn=get_section,
                uri_template="docs://{doc}{#section}",
                name="section",
            )

    def test_reject_path_segment(self):
        """Test that {/segment} is rejected."""

        def get_resource(base: str, segment: str) -> str:
            return f"Base: {base}, Segment: {segment}"

        with pytest.raises(
            ValueError,
            match=r"RFC 6570 level-3 operators \{/\.\.\.\} are not supported",
        ):
            ResourceTemplate.from_function(
                fn=get_resource,
                uri_template="api://{base}{/segment}",
                name="resource",
            )

    def test_reject_label_expansion(self):
        """Test that {.ext} label expansion is rejected."""

        def get_file(name: str, ext: str) -> str:
            return f"File: {name}.{ext}"

        with pytest.raises(
            ValueError,
            match=r"RFC 6570 level-3 operators \{\.\.\.\.\} are not supported",
        ):
            ResourceTemplate.from_function(
                fn=get_file,
                uri_template="files://{name}{.ext}",
                name="file",
            )

    def test_reject_path_style(self):
        """{;param} expands into the path, so it is rejected."""

        def get_data(id: str, p: str = "") -> str:
            return f"id={id} p={p}"

        with pytest.raises(
            ValueError,
            match=r"RFC 6570 level-3 operators \{;\.\.\.\} are not supported",
        ):
            ResourceTemplate.from_function(
                fn=get_data,
                uri_template="x://e/{id}{;p}",
                name="data",
            )

    def test_error_names_supported_forms_with_single_braces(self):
        def get_file(path: str) -> str:
            return path

        with pytest.raises(ValueError) as exc_info:
            ResourceTemplate.from_function(
                fn=get_file, uri_template="files://{+path}", name="file"
            )
        message = str(exc_info.value)
        assert "{?param} or {&param}" in message
        assert "{{" not in message

    def test_reject_mixed_path_and_unsupported(self):
        """Test that templates mixing path params and unsupported operators are rejected."""

        def get_resource(id: str, path: str) -> str:
            return f"ID: {id}, Path: {path}"

        with pytest.raises(
            ValueError,
            match=r"RFC 6570 level-3 operators \{\+\.\.\.\} are not supported",
        ):
            ResourceTemplate.from_function(
                fn=get_resource,
                uri_template="api://resource/{id}/{+path}",
                name="resource",
            )


class TestSetExpansion:
    """Test that set expansion {a,b} is rejected at registration."""

    def test_reject_set_expansion(self):
        """Test that {a,b} set expansion is rejected."""

        def get_values(a: str, b: str) -> str:
            return f"a={a}, b={b}"

        with pytest.raises(
            ValueError,
            match=r"Set expansion syntax like \{a,b\} is not supported",
        ):
            ResourceTemplate.from_function(
                fn=get_values,
                uri_template="data://{a,b}",
                name="values",
            )

    def test_reject_set_expansion_with_path(self):
        """Test that set expansion mixed with path params is rejected."""

        def get_data(id: str, a: str, b: str) -> str:
            return f"ID: {id}, a={a}, b={b}"

        with pytest.raises(
            ValueError,
            match=r"Set expansion syntax like \{a,b\} is not supported",
        ):
            ResourceTemplate.from_function(
                fn=get_data,
                uri_template="data://{id}/{a,b}",
                name="data",
            )


class TestSupportedQueryForms:
    """Test that all query-relevant level-3 forms are accepted."""

    async def test_continuation_param_accepted(self):
        """Test that {&param} continuation is accepted."""

        def get_data(id: str, page: int = 1) -> str:
            return f"ID: {id}, page: {page}"

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="data://{id}{&page}",
            name="data",
        )
        assert template.uri_template == "data://{id}{&page}"
