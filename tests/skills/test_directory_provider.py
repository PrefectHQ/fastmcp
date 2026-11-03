"""Tests for `SkillsDirectoryProvider`, which serves skill folders from disk."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills.provider import SkillsDirectoryProvider


def write_skill(folder: Path, name: str | None = None, body: str = "# Body\n") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_bytes(
        f"---\nname: {name or folder.name}\ndescription: Use {folder.name}\n---\n{body}".encode()
    )
    return folder


def skills_client(server: FastMCP) -> Client:
    return Client(server, extensions=[SkillsClientExtension()])


async def listed(provider: SkillsDirectoryProvider) -> list[str]:
    async with skills_client(FastMCP("Skills", providers=[provider])) as client:
        return [entry.uri for entry in await client.list_skills()]


async def test_serves_folders_at_their_relative_paths(tmp_path: Path):
    write_skill(tmp_path / "review")
    deploy = write_skill(tmp_path / "ops" / "deploy")
    (deploy / "refs").mkdir()
    (deploy / "refs" / "checklist.md").write_bytes(b"- tests\n")

    provider = SkillsDirectoryProvider(tmp_path)
    async with skills_client(FastMCP("Skills", providers=[provider])) as client:
        uris = [entry.uri for entry in await client.list_skills()]
        skill = await client.read_skill("skill://ops/deploy/SKILL.md")

    assert uris == ["skill://ops/deploy/SKILL.md", "skill://review/SKILL.md"]
    assert skill == Skill.from_directory(deploy)


async def test_a_skill_folder_owns_everything_below_it(tmp_path: Path):
    outer = write_skill(tmp_path / "outer")
    write_skill(outer / "examples" / "inner")

    provider = SkillsDirectoryProvider(tmp_path)
    async with skills_client(FastMCP("Skills", providers=[provider])) as client:
        (entry,) = await client.list_skills()
        skill = await client.read_skill(entry.uri)

    assert entry.uri == "skill://outer/SKILL.md"
    assert "examples/inner/SKILL.md" in skill.files


async def test_invalid_folders_are_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    write_skill(tmp_path / "good")
    write_skill(tmp_path / "renamed", name="other")
    (tmp_path / "no-frontmatter").mkdir()
    (tmp_path / "no-frontmatter" / "SKILL.md").write_bytes(b"# Just text\n")
    write_skill(tmp_path / "bad-yaml", body="")
    (tmp_path / "bad-yaml" / "SKILL.md").write_bytes(b"---\nname: [\n---\n")

    assert await listed(SkillsDirectoryProvider(tmp_path)) == ["skill://good/SKILL.md"]
    assert caplog.text.count("Skipping invalid skill") == 3


async def test_first_root_wins(tmp_path: Path):
    write_skill(tmp_path / "a" / "review", body="# First\n")
    write_skill(tmp_path / "b" / "review", body="# Second\n")
    write_skill(tmp_path / "b" / "deploy")

    provider = SkillsDirectoryProvider([tmp_path / "a", tmp_path / "b"])
    async with skills_client(FastMCP("Skills", providers=[provider])) as client:
        uris = [entry.uri for entry in await client.list_skills()]
        skill = await client.read_skill("skill://review/SKILL.md")

    assert uris == ["skill://deploy/SKILL.md", "skill://review/SKILL.md"]
    assert skill.files["SKILL.md"].endswith(b"# First\n")


async def test_skill_nested_in_another_roots_skill_is_skipped(tmp_path: Path):
    write_skill(tmp_path / "a" / "ops")
    write_skill(tmp_path / "b" / "ops" / "deploy")

    provider = SkillsDirectoryProvider([tmp_path / "a", tmp_path / "b"])
    assert await listed(provider) == ["skill://ops/SKILL.md"]


async def test_missing_root_serves_nothing(tmp_path: Path):
    assert await listed(SkillsDirectoryProvider(tmp_path / "missing")) == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")
async def test_symlinked_skill_folder_is_followed(tmp_path: Path):
    target = write_skill(tmp_path / "elsewhere" / "review")
    root = tmp_path / "skills"
    root.mkdir()
    os.symlink(target, root / "review", target_is_directory=True)

    assert await listed(SkillsDirectoryProvider(root)) == ["skill://review/SKILL.md"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")
async def test_symlink_inside_a_skill_rejects_it(tmp_path: Path):
    folder = write_skill(tmp_path / "review")
    (tmp_path / "secret.txt").write_text("secret")
    os.symlink(tmp_path / "secret.txt", folder / "notes.md")

    assert await listed(SkillsDirectoryProvider(tmp_path)) == []


async def test_without_reload_folders_are_read_once(tmp_path: Path):
    folder = write_skill(tmp_path / "review", body="# Before\n")
    server = FastMCP("Skills", providers=[SkillsDirectoryProvider(tmp_path)])
    write_skill(folder, body="# After\n")
    write_skill(tmp_path / "deploy")

    async with skills_client(server) as client:
        uris = [entry.uri for entry in await client.list_skills()]
        skill = await client.read_skill("skill://review/SKILL.md")

    assert uris == ["skill://review/SKILL.md"]
    assert skill.files["SKILL.md"].endswith(b"# Before\n")


async def test_reload_shows_edits_through_the_response_cache(tmp_path: Path):
    folder = write_skill(tmp_path / "review", body="# Before\n")
    server = FastMCP(
        "Skills",
        providers=[SkillsDirectoryProvider(tmp_path, reload=True)],
        middleware=[ResponseCachingMiddleware()],
    )

    async with skills_client(server) as client:
        before = await client.read_skill("skill://review/SKILL.md")
        write_skill(folder, body="# After\n")
        write_skill(tmp_path / "deploy")
        after = await client.read_skill("skill://review/SKILL.md")
        uris = [entry.uri for entry in await client.list_skills()]

    assert before.files["SKILL.md"].endswith(b"# Before\n")
    assert after.files["SKILL.md"].endswith(b"# After\n")
    assert uris == ["skill://deploy/SKILL.md", "skill://review/SKILL.md"]
