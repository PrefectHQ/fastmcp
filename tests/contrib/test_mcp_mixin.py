"""Tests for the MCPMixin class."""

import pytest

from fastmcp import FastMCP
from fastmcp.contrib.mcp_mixin import (
    MCPMixin,
    mcp_prompt,
    mcp_resource,
    mcp_tool,
)
from fastmcp.contrib.mcp_mixin.mcp_mixin import (
    _DEFAULT_SEPARATOR_PROMPT,
    _DEFAULT_SEPARATOR_RESOURCE,
    _DEFAULT_SEPARATOR_TOOL,
)


class TestMCPMixin:
    """Test suite for MCPMixin functionality."""

    def test_initialization(self):
        """Test that a class inheriting MCPMixin can be initialized."""

        class MyMixin(MCPMixin):
            pass

        instance = MyMixin()
        assert instance is not None

    # --- Tool Registration Tests ---
    @pytest.mark.parametrize(
        "prefix, separator, expected_key, unexpected_key",
        [
            (
                None,
                _DEFAULT_SEPARATOR_TOOL,
                "sample_tool",
                f"None{_DEFAULT_SEPARATOR_TOOL}sample_tool",
            ),
            (
                "pref",
                _DEFAULT_SEPARATOR_TOOL,
                f"pref{_DEFAULT_SEPARATOR_TOOL}sample_tool",
                "sample_tool",
            ),
            (
                "pref",
                "-",
                "pref-sample_tool",
                f"pref{_DEFAULT_SEPARATOR_TOOL}sample_tool",
            ),
        ],
        ids=["No prefix", "Default separator", "Custom separator"],
    )
    async def test_tool_registration(
        self, prefix, separator, expected_key, unexpected_key
    ):
        """Test tool registration with prefix and separator variations."""
        mcp = FastMCP()

        class MyToolMixin(MCPMixin):
            @mcp_tool()
            def sample_tool(self):
                pass

        instance = MyToolMixin()
        instance.register_tools(mcp, prefix=prefix, separator=separator)

        registered_tools = await mcp.list_tools()
        assert any(t.name == expected_key for t in registered_tools)
        assert not any(t.name == unexpected_key for t in registered_tools)

    @pytest.mark.parametrize(
        "prefix, separator, expected_uri_key, expected_name, unexpected_uri_key",
        [
            (
                None,
                _DEFAULT_SEPARATOR_RESOURCE,
                "test://resource",
                "sample_resource",
                f"None{_DEFAULT_SEPARATOR_RESOURCE}test://resource",
            ),
            (
                "pref",
                _DEFAULT_SEPARATOR_RESOURCE,
                f"pref{_DEFAULT_SEPARATOR_RESOURCE}test://resource",
                f"pref{_DEFAULT_SEPARATOR_RESOURCE}sample_resource",
                "test://resource",
            ),
            (
                "pref",
                "fff",
                "prefffftest://resource",
                "preffffsample_resource",
                f"pref{_DEFAULT_SEPARATOR_RESOURCE}test://resource",
            ),
        ],
        ids=["No prefix", "Default separator", "Custom separator"],
    )
    async def test_resource_registration(
        self, prefix, separator, expected_uri_key, expected_name, unexpected_uri_key
    ):
        """Test resource registration with prefix and separator variations."""
        mcp = FastMCP()

        class MyResourceMixin(MCPMixin):
            @mcp_resource(uri="test://resource")
            def sample_resource(self):
                pass

        instance = MyResourceMixin()
        instance.register_resources(mcp, prefix=prefix, separator=separator)

        registered_resources = await mcp.list_resources()
        assert any(str(r.uri) == expected_uri_key for r in registered_resources)
        resource = next(
            r for r in registered_resources if str(r.uri) == expected_uri_key
        )
        assert resource.name == expected_name
        assert not any(str(r.uri) == unexpected_uri_key for r in registered_resources)

    @pytest.mark.parametrize(
        "prefix, separator, expected_name, unexpected_name",
        [
            (
                None,
                _DEFAULT_SEPARATOR_PROMPT,
                "sample_prompt",
                f"None{_DEFAULT_SEPARATOR_PROMPT}sample_prompt",
            ),
            (
                "pref",
                _DEFAULT_SEPARATOR_PROMPT,
                f"pref{_DEFAULT_SEPARATOR_PROMPT}sample_prompt",
                "sample_prompt",
            ),
            (
                "pref",
                ":",
                "pref:sample_prompt",
                f"pref{_DEFAULT_SEPARATOR_PROMPT}sample_prompt",
            ),
        ],
        ids=["No prefix", "Default separator", "Custom separator"],
    )
    async def test_prompt_registration(
        self, prefix, separator, expected_name, unexpected_name
    ):
        """Test prompt registration with prefix and separator variations."""
        mcp = FastMCP()

        class MyPromptMixin(MCPMixin):
            @mcp_prompt()
            def sample_prompt(self):
                pass

        instance = MyPromptMixin()
        instance.register_prompts(mcp, prefix=prefix, separator=separator)

        prompts = await mcp.list_prompts()
        assert any(p.name == expected_name for p in prompts)
        assert not any(p.name == unexpected_name for p in prompts)

    async def test_register_all_no_prefix(self):
        """Test register_all method registers all types without a prefix."""
        mcp = FastMCP()

        class MyFullMixin(MCPMixin):
            @mcp_tool()
            def tool_all(self):
                pass

            @mcp_resource(uri="res://all")
            def resource_all(self):
                pass

            @mcp_prompt()
            def prompt_all(self):
                pass

        instance = MyFullMixin()
        instance.register_all(mcp)

        tools = await mcp.list_tools()
        resources = await mcp.list_resources()
        prompts = await mcp.list_prompts()

        assert any(t.name == "tool_all" for t in tools)
        assert any(str(r.uri) == "res://all" for r in resources)
        assert any(p.name == "prompt_all" for p in prompts)

    async def test_register_all_with_prefix_default_separators(self):
        """Test register_all method registers all types with a prefix and default separators."""
        mcp = FastMCP()

        class MyFullMixinPrefixed(MCPMixin):
            @mcp_tool()
            def tool_all_p(self):
                pass

            @mcp_resource(uri="res://all_p")
            def resource_all_p(self):
                pass

            @mcp_prompt()
            def prompt_all_p(self):
                pass

        instance = MyFullMixinPrefixed()
        instance.register_all(mcp, prefix="all")

        tools = await mcp.list_tools()
        resources = await mcp.list_resources()
        prompts = await mcp.list_prompts()

        assert any(t.name == f"all{_DEFAULT_SEPARATOR_TOOL}tool_all_p" for t in tools)
        assert any(
            str(r.uri) == f"all{_DEFAULT_SEPARATOR_RESOURCE}res://all_p"
            for r in resources
        )
        assert any(
            p.name == f"all{_DEFAULT_SEPARATOR_PROMPT}prompt_all_p" for p in prompts
        )

    async def test_register_all_with_prefix_custom_separators(self):
        """Test register_all method registers all types with a prefix and custom separators."""
        mcp = FastMCP()

        class MyFullMixinCustomSep(MCPMixin):
            @mcp_tool()
            def tool_cust(self):
                pass

            @mcp_resource(uri="res://cust")
            def resource_cust(self):
                pass

            @mcp_prompt()
            def prompt_cust(self):
                pass

        instance = MyFullMixinCustomSep()
        instance.register_all(
            mcp,
            prefix="cust",
            tool_separator="-",
            resource_separator="::",
            prompt_separator=".",
        )

        tools = await mcp.list_tools()
        resources = await mcp.list_resources()
        prompts = await mcp.list_prompts()

        assert any(t.name == "cust-tool_cust" for t in tools)
        assert any(str(r.uri) == "cust::res://cust" for r in resources)
        assert any(p.name == "cust.prompt_cust" for p in prompts)

        # Check default separators weren't used
        assert not any(
            t.name == f"cust{_DEFAULT_SEPARATOR_TOOL}tool_cust" for t in tools
        )
        assert not any(
            str(r.uri) == f"cust{_DEFAULT_SEPARATOR_RESOURCE}res://cust"
            for r in resources
        )
        assert not any(
            p.name == f"cust{_DEFAULT_SEPARATOR_PROMPT}prompt_cust" for p in prompts
        )

    async def test_tool_with_title_and_meta(self):
        """Test that title (via annotations) and meta arguments are properly passed through."""
        from mcp.types import ToolAnnotations

        mcp = FastMCP()

        class MyToolWithMeta(MCPMixin):
            @mcp_tool(
                annotations=ToolAnnotations(title="My Tool Title"),
                meta={"version": "1.0", "author": "test"},
            )
            def sample_tool(self):
                pass

        instance = MyToolWithMeta()
        instance.register_tools(mcp)

        registered_tools = await mcp.list_tools()
        tool = next(t for t in registered_tools if t.name == "sample_tool")

        assert tool.annotations is not None
        assert tool.annotations.title == "My Tool Title"
        assert tool.meta == {"version": "1.0", "author": "test"}

    async def test_resource_with_meta(self):
        """Test that meta argument is properly passed through for resources."""
        mcp = FastMCP()

        class MyResourceWithMeta(MCPMixin):
            @mcp_resource(
                uri="test://resource",
                title="My Resource Title",
                meta={"category": "data", "internal": True},
            )
            def sample_resource(self):
                pass

        instance = MyResourceWithMeta()
        instance.register_resources(mcp)

        registered_resources = await mcp.list_resources()
        resource = next(
            r for r in registered_resources if str(r.uri) == "test://resource"
        )

        assert resource.meta == {"category": "data", "internal": True}
        assert resource.title == "My Resource Title"

    async def test_prompt_with_title_and_meta(self):
        """Test that title and meta arguments are properly passed through for prompts."""
        mcp = FastMCP()

        class MyPromptWithMeta(MCPMixin):
            @mcp_prompt(
                title="My Prompt Title",
                meta={"priority": "high", "category": "analysis"},
            )
            def sample_prompt(self):
                pass

        instance = MyPromptWithMeta()
        instance.register_prompts(mcp)

        prompts = await mcp.list_prompts()
        prompt = next(p for p in prompts if p.name == "sample_prompt")

        assert prompt.title == "My Prompt Title"
        assert prompt.meta == {"priority": "high", "category": "analysis"}
