"""The 4.1 skills API keeps working in 4.2 and warns with its replacement."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from fastmcp import Client, FastMCP
from fastmcp._warnings import FastMCPDeprecationWarning
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills.extension import SkillsExtension
from fastmcp.skills.provider import SkillsDirectoryProvider
from fastmcp.utilities.tests import temporary_settings

REVIEW = Skill(name="review", description="Review changes", instructions="# Review\n")


def write_invalid(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Just text\n")
    return folder


async def test_old_directory_provider_warns_and_keeps_serving(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    REVIEW.to_directory(tmp_path / "review")
    write_invalid(tmp_path / "pdf_tools")

    with pytest.warns(FastMCPDeprecationWarning) as record:
        provider = OldDirectoryProvider(tmp_path, reload=True)

    (warning,) = record
    message = str(warning.message)
    assert warning.filename == __file__
    assert "removed in FastMCP 4.3" in message
    assert (
        f"SkillsDirectoryProvider([{str(tmp_path.resolve())!r}], reload=True)"
        in message
    )
    assert "Fix these folders first:" in message
    assert "rename the folder to 'pdf-tools'" in message

    # Still the 4.1 resource behavior, including the folder the new loader rejects.
    async with Client(FastMCP("Skills", providers=[provider])) as client:
        uris = {str(resource.uri) for resource in await client.list_resources()}
    assert {"skill://review/SKILL.md", "skill://pdf_tools/SKILL.md"} <= uris


def test_warning_says_when_every_folder_already_passes(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    REVIEW.to_directory(tmp_path / "review")
    with pytest.warns(FastMCPDeprecationWarning, match="already passes"):
        OldDirectoryProvider(tmp_path)


def test_custom_main_file_is_told_to_rename(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    (tmp_path / "pdf_tools").mkdir()
    (tmp_path / "pdf_tools" / "README.md").write_text("# PDF tools\n")
    with pytest.warns(FastMCPDeprecationWarning) as record:
        OldDirectoryProvider(tmp_path, main_file_name="README.md")
    message = str(record[0].message)
    assert "Rename each `README.md`" in message
    assert "can only be checked once" in message
    assert "already passes" not in message


def test_no_warning_or_scan_when_deprecation_warnings_are_off(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    write_invalid(tmp_path / "pdf_tools")
    with temporary_settings(deprecation_warnings=False):
        with warnings.catch_warnings():
            warnings.simplefilter("error", FastMCPDeprecationWarning)
            OldDirectoryProvider(tmp_path)


async def test_experimental_skills_returns_the_replacement(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    REVIEW.to_directory(tmp_path / "review")
    # The 4.1 experimental setup, verbatim from its docs.
    mcp = FastMCP("Skills")
    with pytest.warns(FastMCPDeprecationWarning, match="experimental_skills") as record:
        skills = OldDirectoryProvider(tmp_path, experimental_skills=True)
    assert record[0].filename == __file__
    assert f"SkillsDirectoryProvider([{str(tmp_path)!r}])" in str(record[0].message)
    mcp.add_provider(skills)
    mcp.add_extension(SkillsExtension(providers=[skills]))

    assert isinstance(skills, SkillsDirectoryProvider)
    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        (entry,) = await client.list_skills()
    assert entry.uri == "skill://review/SKILL.md"


def test_experimental_skills_still_rejects_custom_main_file(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    with pytest.raises(ValueError, match="SKILL.md"):
        OldDirectoryProvider(
            tmp_path, main_file_name="README.md", experimental_skills=True
        )


async def test_old_skill_provider_warns_with_the_fix_and_keeps_serving(
    tmp_path: Path,
):
    from fastmcp.server.providers.skills import SkillProvider

    folder = write_invalid(tmp_path / "pdf_tools")
    with pytest.warns(FastMCPDeprecationWarning) as record:
        provider = SkillProvider(folder)

    (warning,) = record
    assert warning.filename == __file__
    assert "SkillCatalogProvider([Skill.from_directory(" in str(warning.message)
    assert "rename the folder to 'pdf-tools'" in str(warning.message)
    async with Client(FastMCP("Skills", providers=[provider])) as client:
        (content,) = await client.read_resource("skill://pdf_tools/SKILL.md")
    assert "Just text" in getattr(content, "text", "")


def test_vendor_presets_name_their_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    from fastmcp.server.providers import ClaudeSkillsProvider
    from fastmcp.server.providers.skills import CodexSkillsProvider

    with pytest.warns(FastMCPDeprecationWarning) as record:
        CodexSkillsProvider()
        ClaudeSkillsProvider()

    codex, claude = record
    assert str(codex.message).startswith("`CodexSkillsProvider` is deprecated")
    assert repr(str(Path(tmp_path, ".codex", "skills").resolve())) in str(codex.message)
    assert repr(str(Path(tmp_path, ".claude", "skills").resolve())) in str(
        claude.message
    )
    assert codex.filename == claude.filename == __file__


async def test_old_client_helpers_warn_and_keep_working(tmp_path: Path):
    from fastmcp.skills.provider import SkillCatalogProvider
    from fastmcp.utilities.skills import download_skill, list_skills, sync_skills

    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])])
    async with Client(server) as client:
        with pytest.warns(FastMCPDeprecationWarning, match=r"client\.list_skills"):
            (summary,) = await list_skills(client)
        with pytest.warns(FastMCPDeprecationWarning, match="to_directory"):
            downloaded = await download_skill(client, "review", tmp_path / "a")
        with pytest.warns(FastMCPDeprecationWarning, match="read_skill"):
            (synced,) = await sync_skills(client, tmp_path / "b")
    assert summary.name == "review"
    assert Skill.from_directory(downloaded) == REVIEW
    assert Skill.from_directory(synced) == REVIEW


def test_alpha_only_helper_is_gone():
    import fastmcp.utilities.skills as helpers

    assert not hasattr(helpers, "list_skill_summaries")


def test_experimental_skills_refuses_subclasses(tmp_path: Path):
    from fastmcp.server.providers.skills import (
        SkillsDirectoryProvider as OldDirectoryProvider,
    )

    class GuardedProvider(OldDirectoryProvider):
        pass

    with pytest.raises(TypeError, match="GuardedProvider"):
        GuardedProvider(tmp_path, experimental_skills=True)
