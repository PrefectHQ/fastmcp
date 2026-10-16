"""Main files and directory entries in skill providers."""

from pathlib import Path

import pytest
from mcp.types import TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillProvider, SkillsDirectoryProvider
from fastmcp.server.providers.skills._common import SkillPathError


def make_skill(path: Path, content: str = "# Instructions") -> Path:
    path.mkdir()
    (path / "SKILL.md").write_text(content, encoding="utf-8")
    return path


def test_main_file_must_resolve_inside_skill_directory(tmp_path: Path) -> None:
    skill = make_skill(tmp_path / "skill")
    outside = tmp_path / "instructions.md"
    outside.write_text("# Other instructions", encoding="utf-8")
    (skill / "SKILL.md").unlink()
    (skill / "SKILL.md").symlink_to(outside)

    with pytest.raises(SkillPathError):
        SkillProvider(skill)


async def test_main_file_is_checked_again_when_read(tmp_path: Path) -> None:
    skill = make_skill(tmp_path / "skill")
    provider = SkillProvider(skill)
    resource = await provider.get_resource("skill://skill/SKILL.md")
    assert resource is not None
    outside = tmp_path / "instructions.md"
    outside.write_text("# Other instructions", encoding="utf-8")
    (skill / "SKILL.md").unlink()
    (skill / "SKILL.md").symlink_to(outside)

    with pytest.raises(SkillPathError):
        await resource.read()


async def test_directory_keeps_other_skills_when_a_main_file_is_outside(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    make_skill(root / "ordinary")
    outside = make_skill(tmp_path / "other")
    linked = make_skill(root / "linked")
    (linked / "SKILL.md").unlink()
    (linked / "SKILL.md").symlink_to(outside / "SKILL.md")

    provider = SkillsDirectoryProvider(root)
    resources = await provider.list_resources()

    assert {resource.name for resource in resources} == {
        "ordinary/SKILL.md",
        "ordinary/_manifest",
    }


@pytest.mark.parametrize("main_file_name", ["SKILL.md", "docs/MAIN.md"])
async def test_main_file_links_within_skill_directory_remain_readable(
    tmp_path: Path, main_file_name: str
) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    content = "# Instructions\n\nRésumé"
    target = skill / "instructions.md"
    target.write_text(content, encoding="utf-8")
    main_file = skill / main_file_name
    main_file.parent.mkdir(exist_ok=True)
    main_file.symlink_to(target)
    server = FastMCP()
    server.add_provider(SkillProvider(skill, main_file_name=main_file_name))

    async with Client(server) as client:
        result = await client.read_resource(f"skill://skill/{main_file_name}")

    assert isinstance(result[0], TextResourceContents)
    assert result[0].text == content


async def test_explicit_skill_root_can_be_a_directory_link(tmp_path: Path) -> None:
    actual = make_skill(tmp_path / "actual")
    link = tmp_path / "selected"
    link.symlink_to(actual, target_is_directory=True)
    resources = await SkillProvider(link).list_resources()

    assert {resource.name for resource in resources} == {
        "actual/SKILL.md",
        "actual/_manifest",
    }


async def test_directory_links_within_root_remain_discoverable(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    actual = make_skill(root / "actual")
    (root / "linked").symlink_to(actual, target_is_directory=True)
    resources = await SkillsDirectoryProvider(root).list_resources()

    assert {resource.name for resource in resources} == {
        "actual/SKILL.md",
        "actual/_manifest",
    }


async def test_directory_discovers_skill_folders_linked_from_elsewhere(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    make_skill(tmp_path / "checkout")
    (root / "checkout").symlink_to(tmp_path / "checkout", target_is_directory=True)
    resources = await SkillsDirectoryProvider(root).list_resources()
    assert {resource.name for resource in resources} == {
        "checkout/SKILL.md",
        "checkout/_manifest",
    }


async def test_directory_keeps_valid_skills_when_a_link_cannot_resolve(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    make_skill(root / "ordinary")
    unresolved = root / "unresolved"
    unresolved.symlink_to(unresolved, target_is_directory=True)
    resources = await SkillsDirectoryProvider(root).list_resources()
    assert {resource.name for resource in resources} == {
        "ordinary/SKILL.md",
        "ordinary/_manifest",
    }


async def test_main_file_stays_in_the_loaded_directory_after_a_link_swap(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    skill = make_skill(root / "skill")
    outside = make_skill(tmp_path / "other", "# Other instructions")
    provider = SkillsDirectoryProvider(root)
    resource = await provider.get_resource("skill://skill/SKILL.md")
    assert resource is not None
    (skill / "SKILL.md").unlink()
    skill.rmdir()
    skill.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SkillPathError):
        await resource.read()
