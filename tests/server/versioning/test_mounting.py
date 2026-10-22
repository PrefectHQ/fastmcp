"""Tests for versioning in mounted servers."""
# ruff: noqa: F811  # Intentional function redefinition for version testing

from __future__ import annotations

from mcp_types import TextContent

from fastmcp import FastMCP
from fastmcp.prompts.base import Prompt
from fastmcp.resources.base import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.auth import require_scopes
from fastmcp.server.transforms import (
    GetPromptNext,
    GetResourceNext,
    GetResourceTemplateNext,
    GetToolNext,
    Transform,
    VersionFilter,
)
from fastmcp.tools.base import Tool
from fastmcp.utilities.versions import (
    VersionSpec,
)


class TestVersionSorting:
    """Tests for version sorting behavior."""

    async def test_semantic_version_sorting(self):
        """Versions should sort semantically, not lexicographically."""
        mcp = FastMCP()

        # Add versions out of order
        @mcp.tool(version="1")
        def count() -> int:
            return 1

        @mcp.tool(version="10")
        def count() -> int:
            return 10

        @mcp.tool(version="2")
        def count() -> int:
            return 2

        # list_tools returns all versions
        tools = await mcp.list_tools()
        assert len(tools) == 3
        versions = {t.version for t in tools}
        assert versions == {"1", "2", "10"}

        # get_tool returns highest (semantic: 10 > 2 > 1)
        tool = await mcp.get_tool("count")
        assert tool is not None
        assert tool.version == "10"

        # call_tool uses highest version
        result = await mcp.call_tool("count", {})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "10"

    async def test_semver_sorting(self):
        """Full semver versions should sort correctly."""
        mcp = FastMCP()

        @mcp.tool(version="1.2.3")
        def info() -> str:
            return "1.2.3"

        @mcp.tool(version="1.2.10")
        def info() -> str:
            return "1.2.10"

        @mcp.tool(version="1.10.1")
        def info() -> str:
            return "1.10.1"

        # list_tools returns all versions
        tools = await mcp.list_tools()
        assert len(tools) == 3
        versions = {t.version for t in tools}
        assert versions == {"1.2.3", "1.2.10", "1.10.1"}

        # get_tool returns highest: 1.10.1 > 1.2.10 > 1.2.3 (semantic)
        tool = await mcp.get_tool("info")
        assert tool is not None
        assert tool.version == "1.10.1"

    async def test_v_prefix_normalized(self):
        """Versions with 'v' prefix should compare correctly."""
        mcp = FastMCP()

        @mcp.tool(version="v1.0")
        def calc() -> int:
            return 1

        @mcp.tool(version="v2.0")
        def calc() -> int:
            return 2

        # list_tools returns all versions
        tools = await mcp.list_tools()
        assert len(tools) == 2
        versions = {t.version for t in tools}
        assert versions == {"v1.0", "v2.0"}

        # get_tool returns highest
        tool = await mcp.get_tool("calc")
        assert tool is not None
        assert tool.version == "v2.0"


class TestMountedServerVersioning:
    """Tests for versioning in mounted servers (FastMCPProvider)."""

    async def test_mounted_tool_preserves_version(self):
        """Mounted tools should preserve their version info."""
        child = FastMCP("Child")

        @child.tool(version="2.0")
        def add(x: int, y: int) -> int:
            return x + y

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        tools = await parent.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "child_add"
        assert tools[0].version == "2.0"

    async def test_mounted_resource_preserves_version(self):
        """Mounted resources should preserve their version info."""
        child = FastMCP("Child")

        @child.resource("file:///config", version="1.5")
        def config() -> str:
            return "config data"

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        resources = await parent.list_resources()
        assert len(resources) == 1
        assert resources[0].version == "1.5"

    async def test_mounted_prompt_preserves_version(self):
        """Mounted prompts should preserve their version info."""
        child = FastMCP("Child")

        @child.prompt(version="3.0")
        def greet(name: str) -> str:
            return f"Hello, {name}!"

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        prompts = await parent.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].name == "child_greet"
        assert prompts[0].version == "3.0"

    async def test_mounted_get_tool_with_version(self):
        """Should be able to get specific version from mounted server."""
        child = FastMCP("Child")

        @child.tool(version="1.0")
        def calc() -> int:
            return 1

        @child.tool(version="2.0")
        def calc() -> int:
            return 2

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # Get highest version (default)
        tool = await parent.get_tool("child_calc")
        assert tool is not None
        assert tool.version == "2.0"

        # Get specific version
        tool_v1 = await parent.get_tool("child_calc", VersionSpec(eq="1.0"))
        assert tool_v1 is not None
        assert tool_v1.version == "1.0"

    async def test_mounted_multiple_versions_all_returned(self):
        """Mounted server with multiple versions should show all versions."""
        child = FastMCP("Child")

        @child.tool(version="1.0")
        def my_tool() -> str:
            return "v1"

        @child.tool(version="3.0")
        def my_tool() -> str:
            return "v3"

        @child.tool(version="2.0")
        def my_tool() -> str:
            return "v2"

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # list_tools returns all versions
        tools = await parent.list_tools()
        assert len(tools) == 3
        versions = {t.version for t in tools}
        assert versions == {"1.0", "2.0", "3.0"}

        # get_tool returns highest
        tool = await parent.get_tool("child_my_tool")
        assert tool is not None
        assert tool.version == "3.0"

    async def test_mounted_call_tool_uses_highest_version(self):
        """Calling mounted tool should use highest version."""
        child = FastMCP("Child")

        @child.tool(version="1.0")
        def double(x: int) -> int:
            return x * 2

        @child.tool(version="2.0")
        def double(x: int) -> int:
            return x * 2 + 100  # Different behavior

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        result = await parent.call_tool("child_double", {"x": 5})
        # Should use v2.0 which adds 100
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "110"

    async def test_mounted_tool_wrapper_executes_correct_version(self):
        """Calling a specific versioned tool wrapper should execute that version."""
        child = FastMCP("Child")

        @child.tool(version="1.0")
        def calc(x: int) -> int:
            return x * 10  # v1.0 multiplies by 10

        @child.tool(version="2.0")
        def calc(x: int) -> int:
            return x * 100  # v2.0 multiplies by 100

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # Get the v1.0 wrapper specifically
        tools = await parent.list_tools()
        v1_tool = next(
            t for t in tools if t.name == "child_calc" and t.version == "1.0"
        )

        # Calling the v1.0 wrapper should execute v1.0's logic
        result = await v1_tool.run({"x": 5})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "50"  # 5 * 10, not 5 * 100

    async def test_mounted_resource_wrapper_reads_correct_version(self):
        """Reading a specific versioned resource should read that version."""
        from fastmcp.utilities.versions import VersionSpec

        child = FastMCP("Child")

        @child.resource("data:///config", version="1.0")
        def config_v1() -> str:
            return "config-v1-content"

        @child.resource("data:///config", version="2.0")
        def config_v2() -> str:
            return "config-v2-content"

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # Reading with version=1.0 should read v1.0's content
        result = await parent.read_resource(
            "data://child//config", version=VersionSpec(eq="1.0")
        )
        assert result.contents[0].content == "config-v1-content"

        # Reading with version=2.0 should read v2.0's content
        result = await parent.read_resource(
            "data://child//config", version=VersionSpec(eq="2.0")
        )
        assert result.contents[0].content == "config-v2-content"

    async def test_mounted_prompt_wrapper_renders_correct_version(self):
        """Rendering a specific versioned prompt should render that version."""
        from fastmcp.utilities.versions import VersionSpec

        child = FastMCP("Child")

        @child.prompt(version="1.0")
        def greeting(name: str) -> str:
            return f"Hello, {name}!"  # v1.0 says Hello

        @child.prompt(version="2.0")
        def greeting(name: str) -> str:
            return f"Greetings, {name}!"  # v2.0 says Greetings

        parent = FastMCP("Parent")
        parent.mount(child, "child")

        # Rendering with version=1.0 should render v1.0's content
        result = await parent.render_prompt(
            "child_greeting", {"name": "World"}, version=VersionSpec(eq="1.0")
        )
        content = result.messages[0].content
        assert isinstance(content, TextContent) and "Hello, World!" in content.text

        # Rendering with version=2.0 should render v2.0's content
        result = await parent.render_prompt(
            "child_greeting", {"name": "World"}, version=VersionSpec(eq="2.0")
        )
        content = result.messages[0].content
        assert isinstance(content, TextContent) and "Greetings, World!" in content.text

    async def test_deeply_nested_version_forwarding(self):
        """Verify version is correctly forwarded through multiple mount levels."""
        level3 = FastMCP("Level3")

        @level3.tool(version="1.0")
        def calc(x: int) -> int:
            return x * 10  # v1.0 multiplies by 10

        @level3.tool(version="2.0")
        def calc(x: int) -> int:
            return x * 100  # v2.0 multiplies by 100

        level2 = FastMCP("Level2")
        level2.mount(level3, "l3")

        level1 = FastMCP("Level1")
        level1.mount(level2, "l2")

        # All versions should be visible through two levels of mounting
        tools = await level1.list_tools()
        calc_tools = [t for t in tools if "calc" in t.name]
        assert len(calc_tools) == 2
        versions = {t.version for t in calc_tools}
        assert versions == {"1.0", "2.0"}

        # Get v1.0 wrapper through two levels of mounting
        v1_tool = next(t for t in tools if "calc" in t.name and t.version == "1.0")

        # Should execute v1.0 logic, not v2.0
        result = await v1_tool.run({"x": 5})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "50"  # 5 * 10, not 5 * 100


def _make_versioned_child() -> FastMCP:
    """Child server with v1.0 and v2.0 of every component type."""
    child = FastMCP("Child")

    @child.tool(version="1.0")
    def calc() -> str:
        return "tool-v1"

    @child.tool(version="2.0")
    def calc() -> str:
        return "tool-v2"

    @child.resource("data://cfg", version="1.0")
    def cfg() -> str:
        return "res-v1"

    @child.resource("data://cfg", version="2.0")
    def cfg() -> str:
        return "res-v2"

    @child.resource("data://items/{id}", version="1.0")
    def item(id: str) -> str:
        return f"tpl-v1-{id}"

    @child.resource("data://items/{id}", version="2.0")
    def item(id: str) -> str:
        return f"tpl-v2-{id}"

    @child.prompt(version="1.0")
    def greet() -> str:
        return "prompt-v1"

    @child.prompt(version="2.0")
    def greet() -> str:
        return "prompt-v2"

    return child


def _mount_with_filter(child: FastMCP) -> FastMCP:
    """Mount the child and constrain the parent to versions < 3.0."""
    parent = FastMCP("Parent")
    parent.mount(child)
    parent.add_transform(VersionFilter(version_lt="3.0"))
    return parent


class TestMountedVersionFilterWithDisabledLatest:
    """A parent VersionFilter must not hide the child's enabled fallback.

    VersionFilter forwards its range as an explicit version spec to the
    mounted server. That used to suppress the disabled-version fallback inside
    the child, so the parent listed the older enabled version but versionless
    lookups returned None and calls failed.
    """

    async def test_versionless_resolution_falls_back_for_all_component_types(self):
        """Listing v1 and resolving/calling it must agree for all components."""
        child = _make_versioned_child()
        child.disable(version=VersionSpec(eq="2.0"))
        parent = _mount_with_filter(child)

        tools = await parent.list_tools()
        assert [(t.name, t.version) for t in tools] == [("calc", "1.0")]
        resources = await parent.list_resources()
        assert [(str(r.uri), r.version) for r in resources] == [("data://cfg", "1.0")]
        templates = await parent.list_resource_templates()
        assert [(t.uri_template, t.version) for t in templates] == [
            ("data://items/{id}", "1.0")
        ]
        prompts = await parent.list_prompts()
        assert [(p.name, p.version) for p in prompts] == [("greet", "1.0")]

        tool = await parent.get_tool("calc")
        assert tool is not None
        assert tool.version == "1.0"
        resource = await parent.get_resource("data://cfg")
        assert resource is not None
        assert resource.version == "1.0"
        template = await parent.get_resource_template("data://items/{id}")
        assert template is not None
        assert template.version == "1.0"
        prompt = await parent.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "1.0"

        tool_result = await parent.call_tool("calc", {})
        assert isinstance(tool_result.content[0], TextContent)
        assert tool_result.content[0].text == "tool-v1"

        resource_result = await parent.read_resource("data://cfg")
        assert resource_result.contents[0].content == "res-v1"

        template_result = await parent.read_resource("data://items/7")
        assert template_result.contents[0].content == "tpl-v1-7"

        prompt_result = await parent.render_prompt("greet", {})
        prompt_content = prompt_result.messages[0].content
        assert isinstance(prompt_content, TextContent)
        assert prompt_content.text == "prompt-v1"

    async def test_enabled_latest_still_resolves_to_v2(self):
        """Control: with v2 enabled, the filter changes nothing."""
        child = _make_versioned_child()
        parent = _mount_with_filter(child)

        tools = await parent.list_tools()
        assert sorted((t.name, t.version) for t in tools) == [
            ("calc", "1.0"),
            ("calc", "2.0"),
        ]
        resources = await parent.list_resources()
        assert sorted((str(r.uri), r.version) for r in resources) == [
            ("data://cfg", "1.0"),
            ("data://cfg", "2.0"),
        ]
        templates = await parent.list_resource_templates()
        assert sorted((t.uri_template, t.version) for t in templates) == [
            ("data://items/{id}", "1.0"),
            ("data://items/{id}", "2.0"),
        ]
        prompts = await parent.list_prompts()
        assert sorted((p.name, p.version) for p in prompts) == [
            ("greet", "1.0"),
            ("greet", "2.0"),
        ]

        tool = await parent.get_tool("calc")
        assert tool is not None
        assert tool.version == "2.0"
        resource = await parent.get_resource("data://cfg")
        assert resource is not None
        assert resource.version == "2.0"
        template = await parent.get_resource_template("data://items/{id}")
        assert template is not None
        assert template.version == "2.0"
        prompt = await parent.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "2.0"

        tool_result = await parent.call_tool("calc", {})
        assert isinstance(tool_result.content[0], TextContent)
        assert tool_result.content[0].text == "tool-v2"
        resource_result = await parent.read_resource("data://cfg")
        assert resource_result.contents[0].content == "res-v2"
        template_result = await parent.read_resource("data://items/7")
        assert template_result.contents[0].content == "tpl-v2-7"
        prompt_result = await parent.render_prompt("greet", {})
        prompt_content = prompt_result.messages[0].content
        assert isinstance(prompt_content, TextContent)
        assert prompt_content.text == "prompt-v2"

    async def test_disabled_latest_without_parent_filter_still_falls_back(self):
        """Control: mounting alone already falls back to v1."""
        child = _make_versioned_child()
        child.disable(version=VersionSpec(eq="2.0"))
        parent = FastMCP("Parent")
        parent.mount(child)

        tool = await parent.get_tool("calc")
        assert tool is not None
        assert tool.version == "1.0"
        resource = await parent.get_resource("data://cfg")
        assert resource is not None
        assert resource.version == "1.0"
        template = await parent.get_resource_template("data://items/{id}")
        assert template is not None
        assert template.version == "1.0"
        prompt = await parent.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "1.0"

        tool_result = await parent.call_tool("calc", {})
        assert isinstance(tool_result.content[0], TextContent)
        assert tool_result.content[0].text == "tool-v1"

    async def test_explicit_version_requests_are_still_honored(self):
        """Control: an exact version request is not replaced by the fallback."""
        child = _make_versioned_child()
        child.disable(version=VersionSpec(eq="2.0"))
        parent = _mount_with_filter(child)

        v1 = VersionSpec(eq="1.0")
        tool = await parent.get_tool("calc", v1)
        assert tool is not None
        assert tool.version == "1.0"
        resource = await parent.get_resource("data://cfg", v1)
        assert resource is not None
        assert resource.version == "1.0"
        template = await parent.get_resource_template("data://items/{id}", v1)
        assert template is not None
        assert template.version == "1.0"
        prompt = await parent.get_prompt("greet", v1)
        assert prompt is not None
        assert prompt.version == "1.0"

        tool_result = await parent.call_tool("calc", {}, version=v1)
        assert isinstance(tool_result.content[0], TextContent)
        assert tool_result.content[0].text == "tool-v1"
        resource_result = await parent.read_resource("data://cfg", version=v1)
        assert resource_result.contents[0].content == "res-v1"
        prompt_result = await parent.render_prompt("greet", {}, version=v1)
        prompt_content = prompt_result.messages[0].content
        assert isinstance(prompt_content, TextContent)
        assert prompt_content.text == "prompt-v1"

        # An exact request for the disabled version is still not served.
        assert await parent.get_tool("calc", VersionSpec(eq="2.0")) is None
        assert await parent.get_resource("data://cfg", VersionSpec(eq="2.0")) is None
        assert (
            await parent.get_resource_template(
                "data://items/{id}", VersionSpec(eq="2.0")
            )
            is None
        )
        assert await parent.get_prompt("greet", VersionSpec(eq="2.0")) is None

    async def test_filter_added_to_child_directly_still_falls_back(self):
        """Control: a filter on the child itself does not break the fallback."""
        child = _make_versioned_child()
        child.disable(version=VersionSpec(eq="2.0"))
        child.add_transform(VersionFilter(version_lt="3.0"))
        parent = FastMCP("Parent")
        parent.mount(child)

        tool = await parent.get_tool("calc")
        assert tool is not None
        assert tool.version == "1.0"
        resource = await parent.get_resource("data://cfg")
        assert resource is not None
        assert resource.version == "1.0"
        template = await parent.get_resource_template("data://items/{id}")
        assert template is not None
        assert template.version == "1.0"
        prompt = await parent.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "1.0"


class _RefuseLowerLookups(Transform):
    """Lists every component but refuses exact lookups of the lower version.

    Range and versionless lookups still resolve the highest version, so the
    disabled-version fallback is left to walk the listing. The lower version
    is only refused on its own (exact) lookup path.
    """

    @staticmethod
    def _refused(version: VersionSpec | None) -> bool:
        return version is not None and version.eq is not None and version.matches("1.0")

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        if self._refused(version):
            return None
        return await call_next(name, version=version)

    async def get_resource(
        self,
        uri: str,
        call_next: GetResourceNext,
        *,
        version: VersionSpec | None = None,
    ) -> Resource | None:
        if self._refused(version):
            return None
        return await call_next(uri, version=version)

    async def get_resource_template(
        self,
        uri: str,
        call_next: GetResourceTemplateNext,
        *,
        version: VersionSpec | None = None,
    ) -> ResourceTemplate | None:
        if self._refused(version):
            return None
        return await call_next(uri, version=version)

    async def get_prompt(
        self, name: str, call_next: GetPromptNext, *, version: VersionSpec | None = None
    ) -> Prompt | None:
        if self._refused(version):
            return None
        return await call_next(name, version=version)


class _RefuseAllLookups(Transform):
    """Lists every component but refuses to resolve any of them."""

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        return None

    async def get_resource(
        self,
        uri: str,
        call_next: GetResourceNext,
        *,
        version: VersionSpec | None = None,
    ) -> Resource | None:
        return None

    async def get_resource_template(
        self,
        uri: str,
        call_next: GetResourceTemplateNext,
        *,
        version: VersionSpec | None = None,
    ) -> ResourceTemplate | None:
        return None

    async def get_prompt(
        self, name: str, call_next: GetPromptNext, *, version: VersionSpec | None = None
    ) -> Prompt | None:
        return None


def _make_auth_protected_latest_child() -> FastMCP:
    """Child whose v2.0 of every component requires the admin scope."""
    child = FastMCP("Child")

    @child.tool(version="1.0")
    def calc() -> str:
        return "tool-v1"

    @child.tool(version="2.0", auth=require_scopes("admin"))
    def calc() -> str:
        return "tool-v2"

    @child.resource("data://cfg", version="1.0")
    def cfg() -> str:
        return "res-v1"

    @child.resource("data://cfg", version="2.0", auth=require_scopes("admin"))
    def cfg() -> str:
        return "res-v2"

    @child.resource("data://items/{id}", version="1.0")
    def item(id: str) -> str:
        return f"tpl-v1-{id}"

    @child.resource("data://items/{id}", version="2.0", auth=require_scopes("admin"))
    def item(id: str) -> str:
        return f"tpl-v2-{id}"

    @child.prompt(version="1.0")
    def greet() -> str:
        return "prompt-v1"

    @child.prompt(version="2.0", auth=require_scopes("admin"))
    def greet() -> str:
        return "prompt-v2"

    return child


class TestMountedVersionFilterLookupRefusals:
    """A range fallback must not bypass lookup-time refusals."""

    async def test_lookup_only_refusal_is_not_bypassed_by_range_filter(self):
        child = _make_versioned_child()
        child.add_transform(_RefuseAllLookups())
        parent = _mount_with_filter(child)

        # The transform passes listing through, so candidates stay visible.
        assert len(await parent.list_tools()) == 2
        assert len(await parent.list_resources()) == 2
        assert len(await parent.list_resource_templates()) == 2
        assert len(await parent.list_prompts()) == 2

        # Resolution must honor the refusal rather than trusting the listing.
        assert await parent.get_tool("calc") is None
        assert await parent.get_resource("data://cfg") is None
        assert await parent.get_resource_template("data://items/{id}") is None
        assert await parent.get_prompt("greet") is None

    async def test_disabled_highest_does_not_expose_a_refused_lower_version(self):
        """The disabled-highest fallback must re-run the lower version's lookup.

        Listing still advertises v1 (the transform only refuses lookups), and
        v2 is disabled after resolution. Taking v1 straight from the listing
        would serve a component the lookup path refuses.
        """
        import pytest

        from fastmcp.exceptions import NotFoundError

        child = _make_versioned_child()
        child.add_transform(_RefuseLowerLookups())
        child.disable(version=VersionSpec(eq="2.0"))
        parent = _mount_with_filter(child)

        # Listing passes the transform through, so v1 stays advertised.
        assert [(t.name, t.version) for t in await parent.list_tools()] == [
            ("calc", "1.0")
        ]
        assert [(str(r.uri), r.version) for r in await parent.list_resources()] == [
            ("data://cfg", "1.0")
        ]
        assert [
            (t.uri_template, t.version) for t in await parent.list_resource_templates()
        ] == [("data://items/{id}", "1.0")]
        assert [(p.name, p.version) for p in await parent.list_prompts()] == [
            ("greet", "1.0")
        ]

        # v2 is disabled, and v1 is refused by its normal lookup path, so
        # resolution must fail rather than trust the listing.
        assert await parent.get_tool("calc") is None
        assert await parent.get_resource("data://cfg") is None
        assert await parent.get_resource_template("data://items/{id}") is None
        assert await parent.get_prompt("greet") is None

        # The refusal also applies to an explicit lower-version request.
        assert await parent.get_tool("calc", VersionSpec(eq="1.0")) is None
        assert await parent.get_resource("data://cfg", VersionSpec(eq="1.0")) is None
        assert (
            await parent.get_resource_template(
                "data://items/{id}", VersionSpec(eq="1.0")
            )
            is None
        )
        assert await parent.get_prompt("greet", VersionSpec(eq="1.0")) is None

        # Calls and reads fail for the same reason instead of serving v1.
        with pytest.raises(NotFoundError):
            await parent.call_tool("calc", {})
        with pytest.raises(NotFoundError):
            await parent.read_resource("data://cfg")
        with pytest.raises(NotFoundError):
            await parent.render_prompt("greet", {})

    async def test_auth_denied_highest_falls_back_to_public_lower_version(self):
        child = _make_auth_protected_latest_child()
        parent = _mount_with_filter(child)

        tool = await parent.get_tool("calc")
        assert tool is not None
        assert tool.version == "1.0"
        resource = await parent.get_resource("data://cfg")
        assert resource is not None
        assert resource.version == "1.0"
        template = await parent.get_resource_template("data://items/{id}")
        assert template is not None
        assert template.version == "1.0"
        prompt = await parent.get_prompt("greet")
        assert prompt is not None
        assert prompt.version == "1.0"

        # The exact protected version remains denied.
        assert await parent.get_tool("calc", VersionSpec(eq="2.0")) is None
