"""Tests for deprecated include_tags/exclude_tags parameters."""

import pytest

from fastmcp import FastMCP
from fastmcp.server.transforms.visibility import Visibility


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
        """exclude_tags adds a Visibility transform that disables matching tags."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(exclude_tags={"internal"})

        # Should have added a Visibility transform that disables the tag
        enabled_transforms = [t for t in mcp._transforms if isinstance(t, Visibility)]
        assert len(enabled_transforms) == 1
        e = enabled_transforms[0]
        assert e._enabled is False
        assert e.tags == {"internal"}

    def test_include_tags_still_works(self):
        """include_tags adds Visibility transforms for allowlist mode."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(include_tags={"public"})

        # Should have added Visibility transforms for allowlist mode
        # (one to disable all, one to enable matching)
        enabled_transforms = [t for t in mcp._transforms if isinstance(t, Visibility)]
        assert len(enabled_transforms) == 2

        # First should disable all (Visibility.all(False))
        disable_all_transform = enabled_transforms[0]
        assert disable_all_transform._enabled is False
        assert disable_all_transform.match_all is True

        # Second should enable matching tags
        enable_transform = enabled_transforms[1]
        assert enable_transform._enabled is True
        assert enable_transform.tags == {"public"}

    def test_exclude_and_include_both_create_transforms(self):
        """exclude_tags and include_tags both create transforms."""
        with pytest.warns(DeprecationWarning):
            mcp = FastMCP(include_tags={"public"}, exclude_tags={"deprecated"})

        # Should have added transforms for both
        # include_tags creates 2 (disable all + enable matching)
        # exclude_tags creates 1 (disable matching)
        enabled_transforms = [t for t in mcp._transforms if isinstance(t, Visibility)]
        assert len(enabled_transforms) == 3

        # Check we have both tag rules
        tags_in_transforms = [t.tags for t in enabled_transforms if t.tags]
        assert {"public"} in tags_in_transforms
        assert {"deprecated"} in tags_in_transforms
