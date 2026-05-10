"""Tests for vendor-specific skills providers."""

from __future__ import annotations

from pathlib import Path

from fastmcp.server.providers.skills import (
    ClaudeSkillsProvider,
    CodexSkillsProvider,
    CopilotSkillsProvider,
    CursorSkillsProvider,
    GeminiSkillsProvider,
    GooseSkillsProvider,
    OpenCodeSkillsProvider,
    VSCodeSkillsProvider,
)


class TestVendorProviders:
    """Tests for vendor-specific skills providers."""

    def test_cursor_skills_provider_path(self, tmp_path: Path, monkeypatch):
        """Test CursorSkillsProvider uses correct path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = CursorSkillsProvider()
        assert provider._roots == [tmp_path / ".cursor" / "skills"]

    def test_vscode_skills_provider_path(self, tmp_path: Path, monkeypatch):
        """Test VSCodeSkillsProvider uses correct path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = VSCodeSkillsProvider()
        assert provider._roots == [tmp_path / ".copilot" / "skills"]

    def test_codex_skills_provider_paths(self, tmp_path: Path, monkeypatch):
        """Test CodexSkillsProvider uses both system and user paths."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = CodexSkillsProvider()
        # Path.resolve() may add /private on macOS, so compare resolved paths
        expected_roots = [
            Path("/etc/codex/skills").resolve(),
            (tmp_path / ".codex" / "skills").resolve(),
        ]
        assert provider._roots == expected_roots

    def test_gemini_skills_provider_path(self, tmp_path: Path, monkeypatch):
        """Test GeminiSkillsProvider uses correct path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = GeminiSkillsProvider()
        assert provider._roots == [tmp_path / ".gemini" / "skills"]

    def test_goose_skills_provider_path(self, tmp_path: Path, monkeypatch):
        """Test GooseSkillsProvider uses correct path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = GooseSkillsProvider()
        assert provider._roots == [tmp_path / ".config" / "agents" / "skills"]

    def test_copilot_skills_provider_path(self, tmp_path: Path, monkeypatch):
        """Test CopilotSkillsProvider uses correct path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = CopilotSkillsProvider()
        assert provider._roots == [tmp_path / ".copilot" / "skills"]

    def test_opencode_skills_provider_path(self, tmp_path: Path, monkeypatch):
        """Test OpenCodeSkillsProvider uses correct path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = OpenCodeSkillsProvider()
        assert provider._roots == [tmp_path / ".config" / "opencode" / "skills"]

    def test_claude_skills_provider_path(self, tmp_path: Path, monkeypatch):
        """Test ClaudeSkillsProvider uses correct path."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        provider = ClaudeSkillsProvider()
        assert provider._roots == [tmp_path / ".claude" / "skills"]

    def test_all_providers_instantiable(self):
        """Test that all vendor providers can be instantiated."""
        providers = [
            ClaudeSkillsProvider(),
            CursorSkillsProvider(),
            VSCodeSkillsProvider(),
            CodexSkillsProvider(),
            GeminiSkillsProvider(),
            GooseSkillsProvider(),
            CopilotSkillsProvider(),
            OpenCodeSkillsProvider(),
        ]

        for provider in providers:
            assert provider is not None
            assert provider._main_file_name == "SKILL.md"

    def test_all_providers_support_reload(self):
        """Test that all providers support reload parameter."""
        providers = [
            ClaudeSkillsProvider(reload=True),
            CursorSkillsProvider(reload=True),
            VSCodeSkillsProvider(reload=True),
            CodexSkillsProvider(reload=True),
            GeminiSkillsProvider(reload=True),
            GooseSkillsProvider(reload=True),
            CopilotSkillsProvider(reload=True),
            OpenCodeSkillsProvider(reload=True),
        ]

        for provider in providers:
            assert provider._reload is True

    def test_all_providers_support_supporting_files(self):
        """Test that all providers support supporting_files parameter."""
        providers = [
            ClaudeSkillsProvider(supporting_files="resources"),
            CursorSkillsProvider(supporting_files="resources"),
            VSCodeSkillsProvider(supporting_files="resources"),
            CodexSkillsProvider(supporting_files="resources"),
            GeminiSkillsProvider(supporting_files="resources"),
            GooseSkillsProvider(supporting_files="resources"),
            CopilotSkillsProvider(supporting_files="resources"),
            OpenCodeSkillsProvider(supporting_files="resources"),
        ]

        for provider in providers:
            assert provider._supporting_files == "resources"

    async def test_codex_scans_both_paths(self, tmp_path: Path, monkeypatch):
        """Test that CodexSkillsProvider scans both system and user paths."""
        # Mock system path
        system_skills = tmp_path / "etc" / "codex" / "skills"
        system_skills.mkdir(parents=True)
        system_skill = system_skills / "system-skill"
        system_skill.mkdir()
        (system_skill / "SKILL.md").write_text(
            """---
description: System skill
---
# System
"""
        )

        # Mock user path
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        user_skills = fake_home / ".codex" / "skills"
        user_skills.mkdir(parents=True)
        user_skill = user_skills / "user-skill"
        user_skill.mkdir()
        (user_skill / "SKILL.md").write_text(
            """---
description: User skill
---
# User
"""
        )

        # Create provider with mocked paths
        # Override roots before discovery
        provider = CodexSkillsProvider()
        provider._roots = [system_skills, user_skills]
        # Trigger re-discovery with new roots
        provider._discover_skills()

        resources = await provider.list_resources()
        # Should find both skills
        assert len(resources) == 4  # 2 skills * 2 resources each

        resource_names = {r.name for r in resources}
        assert "system-skill/SKILL.md" in resource_names
        assert "user-skill/SKILL.md" in resource_names

    async def test_nonexistent_paths_handled_gracefully(
        self, tmp_path: Path, monkeypatch
    ):
        """Test that non-existent paths don't cause errors."""
        # Use a path that definitely doesn't exist
        nonexistent_home = tmp_path / "nonexistent" / "home"
        monkeypatch.setattr(Path, "home", lambda: nonexistent_home)

        # All providers should handle non-existent paths gracefully
        providers = [
            ClaudeSkillsProvider(),
            CursorSkillsProvider(),
            VSCodeSkillsProvider(),
            GeminiSkillsProvider(),
            GooseSkillsProvider(),
            CopilotSkillsProvider(),
            OpenCodeSkillsProvider(),
        ]

        for provider in providers:
            resources = await provider.list_resources()
            # Should return empty list, not raise exception
            assert isinstance(resources, list)
