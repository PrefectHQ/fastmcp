"""Tests for tag filtering in mounted servers."""

import pytest

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError


class TestParentTagFiltering:
    """Test that parent server tag filters apply recursively to mounted servers."""

    async def test_parent_include_tags_filters_mounted_tools(self):
        """Test that parent include_tags filters out non-matching mounted tools."""
        parent = FastMCP("Parent", include_tags={"allowed"})
        mounted = FastMCP("Mounted")

        @mounted.tool(tags={"allowed"})
        def allowed_tool() -> str:
            return "allowed"

        @mounted.tool(tags={"blocked"})
        def blocked_tool() -> str:
            return "blocked"

        parent.mount(mounted)

        tools = await parent.list_tools()
        tool_names = {t.name for t in tools}
        assert "allowed_tool" in tool_names
        assert "blocked_tool" not in tool_names

        # Verify execution also respects filters
        result = await parent.call_tool("allowed_tool", {})
        assert result.structured_content == {"result": "allowed"}

        with pytest.raises(NotFoundError, match="Unknown tool"):
            await parent.call_tool("blocked_tool", {})

    async def test_parent_exclude_tags_filters_mounted_tools(self):
        """Test that parent exclude_tags filters out matching mounted tools."""
        parent = FastMCP("Parent", exclude_tags={"blocked"})
        mounted = FastMCP("Mounted")

        @mounted.tool(tags={"production"})
        def production_tool() -> str:
            return "production"

        @mounted.tool(tags={"blocked"})
        def blocked_tool() -> str:
            return "blocked"

        parent.mount(mounted)

        tools = await parent.list_tools()
        tool_names = {t.name for t in tools}
        assert "production_tool" in tool_names
        assert "blocked_tool" not in tool_names

    async def test_parent_filters_apply_to_mounted_resources(self):
        """Test that parent tag filters apply to mounted resources."""
        parent = FastMCP("Parent", include_tags={"allowed"})
        mounted = FastMCP("Mounted")

        @mounted.resource("resource://allowed", tags={"allowed"})
        def allowed_resource() -> str:
            return "allowed"

        @mounted.resource("resource://blocked", tags={"blocked"})
        def blocked_resource() -> str:
            return "blocked"

        parent.mount(mounted)

        resources = await parent.list_resources()
        resource_uris = {str(r.uri) for r in resources}
        assert "resource://allowed" in resource_uris
        assert "resource://blocked" not in resource_uris

    async def test_parent_filters_apply_to_mounted_prompts(self):
        """Test that parent tag filters apply to mounted prompts."""
        parent = FastMCP("Parent", exclude_tags={"blocked"})
        mounted = FastMCP("Mounted")

        @mounted.prompt(tags={"allowed"})
        def allowed_prompt() -> str:
            return "allowed"

        @mounted.prompt(tags={"blocked"})
        def blocked_prompt() -> str:
            return "blocked"

        parent.mount(mounted)

        prompts = await parent.list_prompts()
        prompt_names = {p.name for p in prompts}
        assert "allowed_prompt" in prompt_names
        assert "blocked_prompt" not in prompt_names
