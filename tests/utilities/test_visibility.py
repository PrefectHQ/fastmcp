"""Tests for Visibility transform class."""

from fastmcp.server.transforms import Visibility
from fastmcp.tools.tool import Tool


class TestVisibilityBasics:
    """Test basic Visibility functionality."""

    def test_default_all_enabled(self):
        """By default, all components are enabled."""
        v = Visibility()
        tool = Tool(name="test", parameters={})
        assert v.is_enabled(tool) is True

    def test_disable_by_key(self):
        """Disabling by key hides the component."""
        v = Visibility()
        tool = Tool(name="test", parameters={})
        v.disable(keys=["tool:test@"])
        assert v.is_enabled(tool) is False

    def test_disable_by_tag(self):
        """Disabling by tag hides components with that tag."""
        v = Visibility()
        tool = Tool(name="test", parameters={}, tags={"internal"})
        v.disable(tags={"internal"})
        assert v.is_enabled(tool) is False

    def test_disable_tag_no_match(self):
        """Disabling a tag doesn't affect components without it."""
        v = Visibility()
        tool = Tool(name="test", parameters={}, tags={"public"})
        v.disable(tags={"internal"})
        assert v.is_enabled(tool) is True

    def test_enable_removes_from_blocklist(self):
        """Enable removes keys/tags from blocklist."""
        v = Visibility()
        tool = Tool(name="test", parameters={})
        v.disable(keys=["tool:test@"])
        assert v.is_enabled(tool) is False
        v.enable(keys=["tool:test@"])
        assert v.is_enabled(tool) is True


class TestVisibilityAllowlist:
    """Test allowlist mode (only=True)."""

    def test_only_mode_hides_by_default(self):
        """With only=True, non-matching components are hidden."""
        v = Visibility()
        tool = Tool(name="test", parameters={})
        v.enable(keys=["tool:other@"], only=True)
        assert v.is_enabled(tool) is False

    def test_only_mode_shows_matching_key(self):
        """With only=True, matching keys are shown."""
        v = Visibility()
        tool = Tool(name="test", parameters={})
        v.enable(keys=["tool:test@"], only=True)
        assert v.is_enabled(tool) is True

    def test_only_mode_shows_matching_tag(self):
        """With only=True, matching tags are shown."""
        v = Visibility()
        tool = Tool(name="test", parameters={}, tags={"public"})
        v.enable(tags={"public"}, only=True)
        assert v.is_enabled(tool) is True

    def test_only_mode_tag_no_match(self):
        """With only=True, non-matching tags are hidden."""
        v = Visibility()
        tool = Tool(name="test", parameters={}, tags={"internal"})
        v.enable(tags={"public"}, only=True)
        assert v.is_enabled(tool) is False


class TestVisibilityPrecedence:
    """Test blocklist takes precedence over allowlist."""

    def test_blocklist_wins_over_allowlist_key(self):
        """Blocklist key beats allowlist key."""
        v = Visibility()
        tool = Tool(name="test", parameters={})
        v.enable(keys=["tool:test@"], only=True)
        v.disable(keys=["tool:test@"])
        assert v.is_enabled(tool) is False

    def test_blocklist_wins_over_allowlist_tag(self):
        """Blocklist tag beats allowlist tag."""
        v = Visibility()
        tool = Tool(name="test", parameters={}, tags={"public", "deprecated"})
        v.enable(tags={"public"}, only=True)
        v.disable(tags={"deprecated"})
        assert v.is_enabled(tool) is False


class TestVisibilityReset:
    """Test reset functionality."""

    def test_reset_clears_all_filters(self):
        """Reset returns to default state."""
        v = Visibility()
        tool = Tool(name="test", parameters={})
        v.disable(keys=["tool:test@"])
        assert v.is_enabled(tool) is False
        v.reset()
        assert v.is_enabled(tool) is True

    def test_reset_clears_allowlist_mode(self):
        """Reset clears allowlist mode."""
        v = Visibility()
        tool = Tool(name="test", parameters={})
        v.enable(keys=["tool:other@"], only=True)
        assert v.is_enabled(tool) is False
        v.reset()
        assert v.is_enabled(tool) is True
