"""Tests for tag filtering in mounted servers."""

import pytest

from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.utilities.versions import VersionSpec


class TestParentTagFiltering:
    """Test that parent server tag filters apply recursively to mounted servers."""

    async def test_parent_include_tags_filters_mounted_tools(self):
        """Test that parent include_tags filters out non-matching mounted tools."""
        parent = FastMCP("Parent")
        parent.enable(tags={"allowed"}, only=True)
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
        parent = FastMCP("Parent")
        parent.disable(tags={"blocked"})
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
        parent = FastMCP("Parent")
        parent.enable(tags={"allowed"}, only=True)
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
        parent = FastMCP("Parent")
        parent.disable(tags={"blocked"})
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


class TestHiddenParentComponentDoesNotShadowMounted:
    """A hidden parent component must not block the mounted one listed in its place.

    With no namespace, the parent and the mounted server can hold the same
    identity. The listing drops the hidden parent one and shows the mounted
    one, so a request for it must reach the mounted one too.
    """

    @staticmethod
    def servers(version: str | None) -> FastMCP:
        parent, child = FastMCP("Parent"), FastMCP("Child")

        parent.tool(name="t", version=version)(lambda: "parent")
        child.tool(name="t", version=version, tags={"x"})(lambda: "child")
        parent.prompt(name="p", version=version)(lambda: "parent")
        child.prompt(name="p", version=version, tags={"x"})(lambda: "child")
        parent.resource("res://r", name="r", version=version)(lambda: "parent")
        child.resource("res://r", name="r", version=version, tags={"x"})(
            lambda: "child"
        )
        parent.resource("res://t/{a}", name="t", version=version)(lambda a: "parent")
        child.resource("res://t/{a}", name="t", version=version, tags={"x"})(
            lambda a: "child"
        )

        parent.mount(child)
        parent.enable(tags={"x"}, only=True)
        return parent

    @pytest.mark.parametrize(
        ("registered", "requested"),
        [(None, None), (None, "1"), ("1", None), ("1", "1")],
    )
    async def test_mounted_twin_answers(self, registered, requested):
        parent = self.servers(registered)
        spec = VersionSpec(eq=requested) if requested else None

        assert [t.name for t in await parent.list_tools()] == ["t"]
        tool = await parent.get_tool("t", spec)
        prompt = await parent.get_prompt("p", spec)
        resource = await parent.get_resource("res://r", spec)
        template = await parent.get_resource_template("res://t/1", spec)

        assert tool is not None and tool.tags == {"x"}
        assert prompt is not None and prompt.tags == {"x"}
        assert resource is not None and resource.tags == {"x"}
        assert template is not None and template.tags == {"x"}

    async def test_hidden_twins_stay_hidden(self):
        parent, child = FastMCP("Parent"), FastMCP("Child")
        parent.tool(name="t", version="1")(lambda: "parent")
        child.tool(name="t", version="1")(lambda: "child")
        parent.mount(child)
        parent.disable(names={"t"})

        assert await parent.get_tool("t") is None
        assert await parent.get_tool("t", VersionSpec(eq="1")) is None

    async def test_range_request_reaches_the_shown_twin(self):
        parent, child = FastMCP("Parent"), FastMCP("Child")
        parent.tool(name="t", version="2")(lambda: "parent")
        child.tool(name="t", version="2", tags={"x"})(lambda: "child")
        parent.mount(child)
        parent.enable(tags={"x"}, only=True)

        tool = await parent.get_tool("t", VersionSpec(gte="1"))
        assert tool is not None and tool.tags == {"x"}

    async def test_plain_provider_twin_that_refuses_lookup_stays_refused(self):
        """Only a mounted server's twin stands in; it re-resolves when called."""
        from fastmcp.server.providers import Provider
        from fastmcp.tools import Tool

        listed = Tool.from_function(lambda: "provider", name="t", tags={"x"})

        class ListsButRefuses(Provider):
            async def _list_tools(self):
                return [listed]

            async def _get_tool(self, name, version=None):
                return None

        parent = FastMCP("Parent")
        parent.tool(name="t")(lambda: "parent")
        parent.add_provider(ListsButRefuses())
        parent.enable(tags={"x"}, only=True)

        assert await parent.get_tool("t") is None
