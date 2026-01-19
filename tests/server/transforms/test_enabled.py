"""Tests for Enabled transform."""

import pytest

from fastmcp.server.transforms.enabled import Enabled, is_enabled
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec


class TestMatching:
    """Test component matching logic."""

    def test_empty_criteria_matches_nothing(self):
        """Empty criteria is a safe default - matches nothing."""
        t = Enabled(False)
        assert t._matches(Tool(name="anything", parameters={})) is False

    def test_match_all_matches_everything(self):
        """match_all=True matches all components."""
        t = Enabled(False, match_all=True)
        assert t._matches(Tool(name="anything", parameters={})) is True

    def test_match_by_name(self):
        """Matches component by name."""
        t = Enabled(False, names={"foo"})
        assert t._matches(Tool(name="foo", parameters={})) is True
        assert t._matches(Tool(name="bar", parameters={})) is False

    def test_match_by_version(self):
        """Matches component by version."""
        t = Enabled(False, version=VersionSpec(eq="v1"))
        assert t._matches(Tool(name="foo", version="v1", parameters={})) is True
        assert t._matches(Tool(name="foo", version="v2", parameters={})) is False

    def test_match_by_version_spec_exact(self):
        """VersionSpec(eq="v1") matches v1 only."""
        t = Enabled(False, version=VersionSpec(eq="v1"))
        assert t._matches(Tool(name="foo", version="v1", parameters={})) is True
        assert t._matches(Tool(name="foo", version="v2", parameters={})) is False
        assert t._matches(Tool(name="foo", version="v0", parameters={})) is False

    def test_match_by_version_spec_gte(self):
        """VersionSpec(gte="v2") matches v2, v3, but not v1."""
        t = Enabled(False, version=VersionSpec(gte="v2"))
        assert t._matches(Tool(name="foo", version="v1", parameters={})) is False
        assert t._matches(Tool(name="foo", version="v2", parameters={})) is True
        assert t._matches(Tool(name="foo", version="v3", parameters={})) is True

    def test_match_by_version_spec_range(self):
        """VersionSpec(gte="v1", lt="v3") matches v1, v2, but not v3."""
        t = Enabled(False, version=VersionSpec(gte="v1", lt="v3"))
        assert t._matches(Tool(name="foo", version="v0", parameters={})) is False
        assert t._matches(Tool(name="foo", version="v1", parameters={})) is True
        assert t._matches(Tool(name="foo", version="v2", parameters={})) is True
        assert t._matches(Tool(name="foo", version="v3", parameters={})) is False
        assert t._matches(Tool(name="foo", version="v4", parameters={})) is False

    def test_unversioned_does_not_match_version_spec(self):
        """Unversioned components (version=None) don't match a VersionSpec."""
        t = Enabled(False, version=VersionSpec(eq="v1"))
        assert t._matches(Tool(name="foo", parameters={})) is False

        t2 = Enabled(False, version=VersionSpec(gte="v1"))
        assert t2._matches(Tool(name="foo", parameters={})) is False

    def test_match_by_tag(self):
        """Matches if component has any of the specified tags."""
        t = Enabled(False, tags=set({"internal", "deprecated"}))
        assert t._matches(Tool(name="foo", parameters={}, tags={"internal"})) is True
        assert t._matches(Tool(name="foo", parameters={}, tags={"public"})) is False

    def test_match_by_component_type(self):
        """Only matches specified component types."""
        t = Enabled(False, names={"foo"}, components={"prompt"})
        # Tool has key "tool:foo@", not "prompt:foo@"
        assert t._matches(Tool(name="foo", parameters={})) is False

    def test_all_criteria_must_match(self):
        """Multiple criteria use AND logic - all must match."""
        t = Enabled(
            False,
            names={"foo"},
            version=VersionSpec(eq="v1"),
            tags=set({"internal"}),
        )
        # All match
        assert (
            t._matches(Tool(name="foo", version="v1", parameters={}, tags={"internal"}))
            is True
        )
        # Version doesn't match
        assert (
            t._matches(Tool(name="foo", version="v2", parameters={}, tags={"internal"}))
            is False
        )


class TestMarking:
    """Test enabled state marking."""

    def test_disable_marks_as_disabled(self):
        """Enabled(False, ...) marks matching components as disabled."""
        tool = Tool(name="foo", parameters={})
        Enabled(False, names={"foo"})._mark_component(tool)
        assert is_enabled(tool) is False

    def test_enable_marks_as_enabled(self):
        """Enabled(True, ...) marks matching components as enabled."""
        tool = Tool(name="foo", parameters={})
        Enabled(True, names={"foo"})._mark_component(tool)
        assert is_enabled(tool) is True
        assert tool.meta is not None
        assert tool.meta["fastmcp"]["_internal"]["enabled"] is True

    def test_non_matching_unchanged(self):
        """Non-matching components are not modified."""
        tool = Tool(name="bar", parameters={})
        Enabled(False, names={"foo"})._mark_component(tool)
        # No _internal key added
        assert tool.meta is None or "_internal" not in tool.meta.get("fastmcp", {})
        assert is_enabled(tool) is True

    def test_mutates_in_place(self):
        """Marking mutates the component in place."""
        tool = Tool(name="foo", parameters={})
        result = Enabled(False, names={"foo"})._mark_component(tool)
        assert result is tool

    def test_disable_all(self):
        """match_all=True disables all components."""
        tool = Tool(name="anything", parameters={})
        Enabled(False, match_all=True)._mark_component(tool)
        assert is_enabled(tool) is False


class TestOverride:
    """Test that later marks override earlier ones."""

    def test_enable_overrides_disable(self):
        """An enable after disable results in enabled."""
        tool = Tool(name="foo", parameters={})
        Enabled(False, names={"foo"})._mark_component(tool)
        assert is_enabled(tool) is False

        Enabled(True, names={"foo"})._mark_component(tool)
        assert is_enabled(tool) is True

    def test_disable_overrides_enable(self):
        """A disable after enable results in disabled."""
        tool = Tool(name="foo", parameters={})
        Enabled(True, names={"foo"})._mark_component(tool)
        assert is_enabled(tool) is True

        Enabled(False, names={"foo"})._mark_component(tool)
        assert is_enabled(tool) is False


class TestHelperFunctions:
    """Test is_enabled helper."""

    def test_unmarked_is_enabled(self):
        """Components without marks are enabled by default."""
        tool = Tool(name="foo", parameters={})
        assert is_enabled(tool) is True

    def test_filtering_pattern(self):
        """Common pattern: filter list with is_enabled."""
        tools = [
            Tool(name="enabled", parameters={}),
            Tool(name="disabled", parameters={}),
        ]
        Enabled(False, names={"disabled"})._mark_component(tools[1])

        visible = [t for t in tools if is_enabled(t)]
        assert [t.name for t in visible] == ["enabled"]


class TestMetadata:
    """Test metadata handling."""

    def test_internal_metadata_stripped_by_get_meta(self):
        """Internal metadata is stripped when calling get_meta()."""
        tool = Tool(name="foo", parameters={})
        Enabled(True, names={"foo"})._mark_component(tool)

        # Raw meta has _internal
        assert tool.meta is not None
        assert "_internal" in tool.meta.get("fastmcp", {})

        # get_meta() strips it
        output = tool.get_meta()
        assert "_internal" not in output.get("fastmcp", {})

    def test_user_metadata_preserved(self):
        """User-provided metadata is not affected."""
        tool = Tool(name="foo", parameters={}, meta={"custom": "value"})
        marked = Enabled(False, names={"foo"})._mark_component(tool)

        assert marked.meta is not None
        assert marked.meta["custom"] == "value"


class TestRepr:
    """Test string representation."""

    def test_repr_disable(self):
        """Repr shows disable action and criteria."""
        t = Enabled(False, names={"foo"})
        r = repr(t)
        assert "disable" in r
        assert "foo" in r

    def test_repr_enable(self):
        """Repr shows enable action."""
        t = Enabled(True, names={"foo"})
        assert "enable" in repr(t)

    def test_repr_match_all(self):
        """Repr shows match_all."""
        t = Enabled(False, match_all=True)
        assert "match_all=True" in repr(t)


class TestTransformChain:
    """Test Enabled in async transform chains."""

    @pytest.fixture
    def tools(self):
        return [
            Tool(name="public", parameters={}, tags={"public"}),
            Tool(name="internal", parameters={}, tags={"internal"}),
            Tool(name="safe_internal", parameters={}, tags={"internal", "safe"}),
        ]

    async def test_list_tools_marks_matching(self, tools):
        """list_tools applies marks to matching components."""
        disable_internal = Enabled(False, tags=set({"internal"}))

        result = await disable_internal.list_tools(tools)

        assert len(result) == 3
        assert is_enabled(result[0])  # public
        assert not is_enabled(result[1])  # internal
        assert not is_enabled(result[2])  # safe_internal

    async def test_later_transform_overrides(self, tools):
        """Later transforms in chain override earlier ones."""
        disable_internal = Enabled(False, tags=set({"internal"}))
        enable_safe = Enabled(True, tags=set({"safe"}))

        # Apply transforms sequentially
        after_disable = await disable_internal.list_tools(tools)
        result = await enable_safe.list_tools(after_disable)
        enabled = [t for t in result if is_enabled(t)]

        # public: never disabled
        # internal: disabled, stays disabled
        # safe_internal: disabled then re-enabled
        assert {t.name for t in enabled} == {"public", "safe_internal"}

    async def test_allowlist_pattern(self, tools):
        """Disable all, then enable specific = allowlist."""
        disable_all = Enabled(False, match_all=True)
        enable_public = Enabled(True, tags=set({"public"}))

        # Apply transforms sequentially
        after_disable = await disable_all.list_tools(tools)
        result = await enable_public.list_tools(after_disable)
        enabled = [t for t in result if is_enabled(t)]

        assert [t.name for t in enabled] == ["public"]
