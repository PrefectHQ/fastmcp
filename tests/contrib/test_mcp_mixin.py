"""Tests for the MCPMixin class."""

import inspect

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
    _PROMPT_VALID_KWARGS,
    _RESOURCE_VALID_KWARGS,
    _TOOL_VALID_KWARGS,
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


class TestMCPMixinKwargsSync:
    """Verify that the valid-kwarg sets stay in sync with from_function signatures."""

    def test_tool_valid_kwargs_match_from_function(self):
        from fastmcp.tools.tool import Tool

        expected = frozenset(
            p for p in inspect.signature(Tool.from_function).parameters if p != "fn"
        )
        assert _TOOL_VALID_KWARGS == expected

    def test_resource_valid_kwargs_match_from_function(self):
        from fastmcp.resources.resource import Resource

        expected = frozenset(
            p
            for p in inspect.signature(Resource.from_function).parameters
            if p not in ("fn", "uri")
        )
        assert _RESOURCE_VALID_KWARGS == expected

    def test_prompt_valid_kwargs_match_from_function(self):
        from fastmcp.prompts.prompt import Prompt

        expected = frozenset(
            p for p in inspect.signature(Prompt.from_function).parameters if p != "fn"
        )
        assert _PROMPT_VALID_KWARGS == expected


class TestMCPMixinValidation:
    """Unknown kwargs raise TypeError at decoration time, not at registration."""

    def test_mcp_tool_rejects_unknown_param(self):
        with pytest.raises(TypeError, match="unexpected keyword argument"):

            @mcp_tool(definitely_not_a_real_param="oops")
            def my_tool(self):
                pass

    def test_mcp_resource_rejects_unknown_param(self):
        with pytest.raises(TypeError, match="unexpected keyword argument"):

            @mcp_resource(uri="test://x", definitely_not_a_real_param="oops")
            def my_resource(self):
                pass

    def test_mcp_prompt_rejects_unknown_param(self):
        with pytest.raises(TypeError, match="unexpected keyword argument"):

            @mcp_prompt(definitely_not_a_real_param="oops")
            def my_prompt(self):
                pass

    def test_error_raised_at_decoration_not_registration(self):
        """The TypeError must surface when the decorator is applied, not later."""
        with pytest.raises(TypeError):

            class MyMixin(MCPMixin):
                @mcp_tool(bad_kwarg=True)
                def tool(self):
                    pass


class TestMCPMixinEnabled:
    """enabled=False suppresses registration; enabled=True (default) registers normally."""

    async def test_tool_enabled_false_skips_registration(self):
        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_tool(enabled=False)
            def hidden_tool(self):
                pass

            @mcp_tool()
            def visible_tool(self):
                pass

        MyMixin().register_tools(mcp)
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert "visible_tool" in names
        assert "hidden_tool" not in names

    async def test_resource_enabled_false_skips_registration(self):
        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_resource(uri="test://hidden", enabled=False)
            def hidden_resource(self):
                pass

            @mcp_resource(uri="test://visible")
            def visible_resource(self):
                pass

        MyMixin().register_resources(mcp)
        resources = await mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert "test://visible" in uris
        assert "test://hidden" not in uris

    async def test_prompt_enabled_false_skips_registration(self):
        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_prompt(enabled=False)
            def hidden_prompt(self):
                pass

            @mcp_prompt()
            def visible_prompt(self):
                pass

        MyMixin().register_prompts(mcp)
        prompts = await mcp.list_prompts()
        names = {p.name for p in prompts}
        assert "visible_prompt" in names
        assert "hidden_prompt" not in names

    async def test_tool_enabled_true_registers_normally(self):
        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_tool(enabled=True)
            def my_tool(self):
                pass

        MyMixin().register_tools(mcp)
        tools = await mcp.list_tools()
        assert any(t.name == "my_tool" for t in tools)


class TestMCPMixinNewParams:
    """Parameters that were previously missing now work end-to-end."""

    async def test_tool_auth_param_forwarded(self):
        from fastmcp.server.auth import require_scopes

        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_tool(auth=require_scopes("write"))
            def secure_tool(self):
                return "ok"

        MyMixin().register_tools(mcp)
        # list_tools() filters by auth context; check internal provider directly
        tools = await mcp.local_provider.list_tools()
        assert any(t.name == "secure_tool" for t in tools)

    async def test_tool_timeout_param_forwarded(self):
        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_tool(timeout=5.0)
            def timed_tool(self):
                return "ok"

        MyMixin().register_tools(mcp)
        tools = await mcp.list_tools()
        assert any(t.name == "timed_tool" for t in tools)

    async def test_tool_version_param_forwarded(self):
        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_tool(version="2.0")
            def versioned_tool(self):
                return "ok"

        MyMixin().register_tools(mcp)
        tools = await mcp.list_tools()
        assert any(t.name == "versioned_tool" for t in tools)

    async def test_resource_auth_param_forwarded(self):
        from fastmcp.server.auth import require_scopes

        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_resource(uri="test://secure", auth=require_scopes("read"))
            def secure_resource(self):
                return "data"

        MyMixin().register_resources(mcp)
        # list_resources() filters by auth context; check internal provider directly
        resources = await mcp.local_provider.list_resources()
        assert any(str(r.uri) == "test://secure" for r in resources)

    async def test_prompt_auth_param_forwarded(self):
        from fastmcp.server.auth import require_scopes

        mcp = FastMCP()

        class MyMixin(MCPMixin):
            @mcp_prompt(auth=require_scopes("read"))
            def secure_prompt(self):
                return "prompt text"

        MyMixin().register_prompts(mcp)
        # list_prompts() filters by auth context; check internal provider directly
        prompts = await mcp.local_provider.list_prompts()
        assert any(p.name == "secure_prompt" for p in prompts)
