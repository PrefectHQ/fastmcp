"""The removed 4.1 skills import paths warn with the replacement."""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmcp import Client, FastMCP
from fastmcp._warnings import FastMCPDeprecationWarning
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills.provider import SkillCatalogProvider, SkillsDirectoryProvider

REVIEW = Skill(name="review", description="Review changes", instructions="# Review\n")


def write_review(root: Path) -> Path:
    return REVIEW.to_directory(root / "review")


def test_old_directory_provider_warns_and_serves(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    write_review(tmp_path)
    with pytest.warns(FastMCPDeprecationWarning, match="fastmcp.skills.provider"):
        provider = OldDirectoryProvider(
            tmp_path, True, supporting_files="resources", experimental_skills=True
        )
    assert isinstance(provider, SkillsDirectoryProvider)
    assert provider._reload


def test_old_directory_provider_rejects_custom_main_file(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    with (
        pytest.warns(FastMCPDeprecationWarning),
        pytest.raises(ValueError, match="SKILL.md"),
    ):
        OldDirectoryProvider(tmp_path, main_file_name="README.md")


def test_old_skill_provider_serves_one_folder(tmp_path: Path):
    from fastmcp.server.providers.skills import SkillProvider

    folder = write_review(tmp_path)
    with pytest.warns(FastMCPDeprecationWarning, match="Skill.from_directory"):
        provider = SkillProvider(folder)
    assert isinstance(provider, SkillCatalogProvider)


def test_vendor_preset_names_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from fastmcp.server.providers import ClaudeSkillsProvider
    from fastmcp.server.providers.skills import CodexSkillsProvider

    with pytest.warns(FastMCPDeprecationWarning, match=r"\.codex/skills"):
        provider = CodexSkillsProvider()
    assert isinstance(provider, SkillsDirectoryProvider)
    with pytest.warns(FastMCPDeprecationWarning, match=r"\.claude/skills"):
        claude = ClaudeSkillsProvider()
    assert isinstance(claude, ClaudeSkillsProvider)
    assert not isinstance(claude, CodexSkillsProvider)


async def test_sync_and_download_still_install(tmp_path: Path):
    from fastmcp.utilities.skills import download_skill, sync_skills

    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])])
    async with Client(server, extensions=[SkillsClientExtension()]) as client:
        with pytest.warns(FastMCPDeprecationWarning, match="read_skill"):
            (synced,) = await sync_skills(client, tmp_path / "a")
        with pytest.warns(FastMCPDeprecationWarning, match="to_directory"):
            downloaded = await download_skill(client, "review", tmp_path / "b")
    assert Skill.from_directory(synced) == REVIEW
    assert Skill.from_directory(downloaded) == REVIEW


async def test_sync_skills_installs_from_a_server_without_the_extension(
    tmp_path: Path,
):
    from fastmcp.utilities.skills import sync_skills

    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])])
    async with Client(server) as client:
        with pytest.warns(FastMCPDeprecationWarning):
            (synced,) = await sync_skills(client, tmp_path)
    assert Skill.from_directory(synced) == REVIEW


def test_old_skill_provider_names_the_fix(tmp_path: Path):
    from fastmcp.server.providers.skills import SkillProvider

    folder = tmp_path / "pdf_tools"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# PDF tools\n")
    with (
        pytest.warns(FastMCPDeprecationWarning),
        pytest.raises(ValueError, match="rename the folder to 'pdf-tools'"),
    ):
        SkillProvider(folder)


def test_removed_helpers_name_their_replacement():
    with pytest.raises(ImportError, match=r"client\.list_skills\(\)"):
        from fastmcp.utilities.skills import list_skill_summaries  # noqa: F401
