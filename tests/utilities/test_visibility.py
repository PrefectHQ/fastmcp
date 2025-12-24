"""Tests for VisibilityFilter class."""

from fastmcp.tools.tool import Tool
from fastmcp.utilities.visibility import VisibilityFilter


class TestVisibilityFilterBasics:
    """Test basic VisibilityFilter functionality."""

    def test_default_all_enabled(self):
        """By default, all components are enabled."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={})
        assert vf.is_enabled(tool) is True

    def test_disable_by_key(self):
        """Disabling by key hides the component."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={})
        vf.disable(keys=["tool:test"])
        assert vf.is_enabled(tool) is False

    def test_disable_by_tag(self):
        """Disabling by tag hides components with that tag."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={}, tags={"internal"})
        vf.disable(tags={"internal"})
        assert vf.is_enabled(tool) is False

    def test_disable_tag_no_match(self):
        """Disabling a tag doesn't affect components without it."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={}, tags={"public"})
        vf.disable(tags={"internal"})
        assert vf.is_enabled(tool) is True

    def test_enable_removes_from_blocklist(self):
        """Enable removes keys/tags from blocklist."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={})
        vf.disable(keys=["tool:test"])
        assert vf.is_enabled(tool) is False
        vf.enable(keys=["tool:test"])
        assert vf.is_enabled(tool) is True


class TestVisibilityFilterAllowlist:
    """Test allowlist mode (only=True)."""

    def test_only_mode_hides_by_default(self):
        """With only=True, non-matching components are hidden."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={})
        vf.enable(keys=["tool:other"], only=True)
        assert vf.is_enabled(tool) is False

    def test_only_mode_shows_matching_key(self):
        """With only=True, matching keys are shown."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={})
        vf.enable(keys=["tool:test"], only=True)
        assert vf.is_enabled(tool) is True

    def test_only_mode_shows_matching_tag(self):
        """With only=True, matching tags are shown."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={}, tags={"public"})
        vf.enable(tags={"public"}, only=True)
        assert vf.is_enabled(tool) is True

    def test_only_mode_tag_no_match(self):
        """With only=True, non-matching tags are hidden."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={}, tags={"internal"})
        vf.enable(tags={"public"}, only=True)
        assert vf.is_enabled(tool) is False


class TestVisibilityFilterPrecedence:
    """Test blocklist takes precedence over allowlist."""

    def test_blocklist_wins_over_allowlist_key(self):
        """Blocklist key beats allowlist key."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={})
        vf.enable(keys=["tool:test"], only=True)
        vf.disable(keys=["tool:test"])
        assert vf.is_enabled(tool) is False

    def test_blocklist_wins_over_allowlist_tag(self):
        """Blocklist tag beats allowlist tag."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={}, tags={"public", "deprecated"})
        vf.enable(tags={"public"}, only=True)
        vf.disable(tags={"deprecated"})
        assert vf.is_enabled(tool) is False


class TestVisibilityFilterReset:
    """Test reset functionality."""

    def test_reset_clears_all_filters(self):
        """Reset returns to default state."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={})
        vf.disable(keys=["tool:test"])
        assert vf.is_enabled(tool) is False
        vf.reset()
        assert vf.is_enabled(tool) is True

    def test_reset_clears_allowlist_mode(self):
        """Reset clears allowlist mode."""
        vf = VisibilityFilter()
        tool = Tool(name="test", parameters={})
        vf.enable(keys=["tool:other"], only=True)
        assert vf.is_enabled(tool) is False
        vf.reset()
        assert vf.is_enabled(tool) is True
