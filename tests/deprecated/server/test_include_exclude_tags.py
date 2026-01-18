"""Tests for deprecated include_tags/exclude_tags parameters."""

import pytest

from fastmcp import FastMCP
from fastmcp.server.transforms.enabled import Enabled


class TestIncludeExcludeTagsDeprecation:
    """Test that include_tags/exclude_tags emit deprecation warnings but still work."""

    def test_exclude_tags_emits_warning(self):
        """exclude_tags parameter emits deprecation warning."""
        with pytest.warns(DeprecationWarning, match="exclude_tags.*deprecated"):
            FastMCP(exclude_tags={"internal"})

    def test_include_tags_emits_warning(self):
        """include_tags parameter emits deprecation warning."""
        with pytest.warns(DeprecationWarning, match="include_tags.*deprecated"):
            FastMCP(include_tags={"public"})

    def test_exclude_tags_still_works(self):
        """exclude_tags adds an Enabled transform that disables matching tags."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(exclude_tags={"internal"})

        # Should have added an Enabled transform that disables the tag
        enabled_transforms = [t for t in mcp._transforms if isinstance(t, Enabled)]
        assert len(enabled_transforms) == 1
        e = enabled_transforms[0]
        assert e._enabled is False
        assert e.tags == frozenset({"internal"})

    def test_include_tags_still_works(self):
        """include_tags adds Enabled transforms for allowlist mode."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(include_tags={"public"})

        # Should have added Enabled transforms for allowlist mode
        # (one to disable all, one to enable matching)
        enabled_transforms = [t for t in mcp._transforms if isinstance(t, Enabled)]
        assert len(enabled_transforms) == 2

        # First should disable all (Enabled.all(False))
        disable_all_transform = enabled_transforms[0]
        assert disable_all_transform._enabled is False
        assert disable_all_transform.match_all is True

        # Second should enable matching tags
        enable_transform = enabled_transforms[1]
        assert enable_transform._enabled is True
        assert enable_transform.tags == frozenset({"public"})

    def test_exclude_and_include_both_create_transforms(self):
        """exclude_tags and include_tags both create transforms."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(include_tags={"public"}, exclude_tags={"deprecated"})

        # Should have added transforms for both
        # include_tags creates 2 (disable all + enable matching)
        # exclude_tags creates 1 (disable matching)
        enabled_transforms = [t for t in mcp._transforms if isinstance(t, Enabled)]
        assert len(enabled_transforms) == 3

        # Check we have both tag rules
        tags_in_transforms = {frozenset(t.tags) for t in enabled_transforms if t.tags}
        assert frozenset({"public"}) in tags_in_transforms
        assert frozenset({"deprecated"}) in tags_in_transforms
