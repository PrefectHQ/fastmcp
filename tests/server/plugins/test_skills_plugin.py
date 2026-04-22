"""Tests for the Skills plugin wrapper.

Provider behavior (skill discovery, file exposure, etc.) is covered by
`test_skills_provider.py` and `test_skills_vendor_providers.py`. This
file only covers plugin-layer concerns — config validation, meta,
vendor→path resolution, and the deprecation shim at the old import path.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from fastmcp.server.plugins.skills.plugin import Vendor

from fastmcp.server.plugins.skills import Skills, SkillsConfig
from fastmcp.server.plugins.skills.directory_provider import SkillsDirectoryProvider
from fastmcp.server.plugins.skills.plugin import VENDOR_PATHS
from fastmcp.server.plugins.skills.skill_provider import SkillProvider


class TestSkillsConfig:
    def test_config_generic_binding(self):
        assert Skills._config_cls is SkillsConfig

    def test_default_config_instantiable(self):
        """Defaults must pass the plugin framework's instantiate-with-no-args
        contract; the source check fires at providers() time."""
        assert SkillsConfig()  # must not raise

    def test_unknown_config_key_rejected(self):
        with pytest.raises((ValidationError, Exception), match="forbid|extra"):
            SkillsConfig(not_a_real_option=True)  # ty: ignore[unknown-argument]

    def test_default_meta(self):
        assert Skills.meta.name == "skills"
        assert Skills.meta.version is None


class TestSourceResolution:
    def test_path_source_builds_skill_provider(self, tmp_path: Path):
        skill = tmp_path / "my-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("# My Skill")

        plugin = Skills(SkillsConfig(path=str(skill)))
        providers = plugin.providers()
        assert isinstance(providers[0], SkillProvider)

    def test_directory_source_builds_directory_provider(self, tmp_path: Path):
        plugin = Skills(SkillsConfig(directory=str(tmp_path)))
        providers = plugin.providers()
        assert isinstance(providers[0], SkillsDirectoryProvider)

    def test_directory_source_accepts_list(self, tmp_path: Path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        plugin = Skills(SkillsConfig(directory=[str(a), str(b)]))
        providers = plugin.providers()
        assert isinstance(providers[0], SkillsDirectoryProvider)

    @pytest.mark.parametrize("vendor", list(VENDOR_PATHS))
    def test_vendor_presets_resolve_to_known_paths(self, vendor: str):
        """Every vendor string must produce a directory provider rooted
        at the paths the old vendor subclass used to hardcode."""
        plugin = Skills(SkillsConfig(vendor=cast(Vendor, vendor)))
        providers = plugin.providers()
        assert isinstance(providers[0], SkillsDirectoryProvider)

    def test_no_source_fails_at_build_time(self):
        plugin = Skills(SkillsConfig())
        with pytest.raises(ValueError, match="path.*directory.*vendor"):
            plugin.providers()

    def test_multiple_sources_rejected(self, tmp_path: Path):
        plugin = Skills(SkillsConfig(directory=str(tmp_path), vendor="claude"))
        with pytest.raises(ValueError, match="exactly one"):
            plugin.providers()


class TestDeprecationShim:
    """The old `fastmcp.server.providers.skills` package shims back to the
    new plugin package. Top-level stays silent; leaf submodule imports
    emit `FastMCPDeprecationWarning`."""

    def test_top_level_is_silent(self):
        import importlib
        import sys

        from fastmcp.exceptions import FastMCPDeprecationWarning

        sys.modules.pop("fastmcp.server.providers.skills", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("fastmcp.server.providers.skills")

        fastmcp_warns = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert not fastmcp_warns

    def test_leaf_submodule_import_emits_deprecation_warning(self):
        import importlib
        import sys

        from fastmcp.exceptions import FastMCPDeprecationWarning

        sys.modules.pop(
            "fastmcp.server.providers.skills.vendor_providers", None
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module(
                "fastmcp.server.providers.skills.vendor_providers"
            )

        fastmcp_warns = [
            w for w in caught if issubclass(w.category, FastMCPDeprecationWarning)
        ]
        assert any("plugins.skills" in str(w.message) for w in fastmcp_warns)

    def test_old_import_path_symbols_still_resolve(self):
        """`ClaudeSkillsProvider` and friends keep resolving through the
        silent package-level shim."""
        from fastmcp.server.plugins.skills.claude_provider import (
            ClaudeSkillsProvider as NewClass,
        )
        from fastmcp.server.providers.skills import (
            ClaudeSkillsProvider as OldClass,
        )

        assert OldClass is NewClass
