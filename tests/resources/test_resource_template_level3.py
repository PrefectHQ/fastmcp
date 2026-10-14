"""Tests for RFC 6570 level-3 operator support in resource templates."""

import pytest

from fastmcp.resources import ResourceTemplate
from fastmcp.resources.template import extract_query_params


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

    def test_extract_form_style_query_params(self):
        """Test extraction from {;param1,param2} syntax."""
        params = extract_query_params("data://items{;page,size}")
        assert params == {"page", "size"}

    def test_extract_mixed_query_forms(self):
        """Test extraction from mixed query-relevant forms."""
        params = extract_query_params("data://items{?filter}{&page}{;sort}")
        assert params == {"filter", "page", "sort"}

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

    async def test_form_style_param_accepted(self):
        """Test that {;param} form-style query is accepted."""

        def get_data(id: str, sort: str = "asc") -> str:
            return f"ID: {id}, sort: {sort}"

        template = ResourceTemplate.from_function(
            fn=get_data,
            uri_template="data://{id}{;sort}",
            name="data",
        )
        assert template.uri_template == "data://{id}{;sort}"

    async def test_mixed_query_forms_accepted(self):
        """Test that mixed query-relevant forms are accepted together."""

        def get_items(
            category: str, page: int = 1, size: int = 10, sort: str = "asc"
        ) -> str:
            return f"Category: {category}, page: {page}, size: {size}, sort: {sort}"

        template = ResourceTemplate.from_function(
            fn=get_items,
            uri_template="items://{category}{?page}{&size}{;sort}",
            name="items",
        )
        assert template.uri_template == "items://{category}{?page}{&size}{;sort}"
