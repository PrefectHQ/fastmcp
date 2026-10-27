from pathlib import Path
from typing import Any

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills.extension import SkillsExtension


@pytest.mark.parametrize("explicit_default", [False, True])
async def test_default_provider_keeps_legacy_publication(
    tmp_path: Path, explicit_default: bool
):
    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"# No frontmatter\r\nLoose instructions\r\n")
    kwargs: dict[str, Any] = {"experimental_skills": False} if explicit_default else {}
    provider = SkillsDirectoryProvider(tmp_path, **kwargs)
    server = FastMCP("Legacy skills", providers=[provider])
    async with Client(server) as client:
        resources = await client.list_resources()
        assert {str(resource.uri) for resource in resources} == {
            "skill://review/SKILL.md",
            "skill://review/_manifest",
        }
        contents = await client.read_resource("skill://review/SKILL.md")
        assert contents[0].text == "# No frontmatter\r\nLoose instructions\r\n"
        assert client.server_capabilities is not None
        assert "io.modelcontextprotocol/skills" not in (
            client.server_capabilities.extensions or {}
        )
    with pytest.raises(ValueError, match="experimental_skills=True"):
        server.add_extension(SkillsExtension(providers=[provider]))


def test_experimental_mode_requires_standard_main_file(tmp_path: Path):
    with pytest.raises(ValueError, match="main_file_name"):
        SkillsDirectoryProvider(
            tmp_path, main_file_name="README.md", experimental_skills=True
        )


async def test_experimental_discovery_is_additive_to_resources(tmp_path: Path):
    directory = tmp_path / "review"
    directory.mkdir()
    (directory / "SKILL.md").write_bytes(b"# Permissive existing skill\n")
    (directory / "notes.txt").write_text("supporting content")
    provider = SkillsDirectoryProvider(tmp_path, experimental_skills=True)
    server = FastMCP("Skills", providers=[provider])
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        assert {str(resource.uri) for resource in await client.list_resources()} == {
            "skill://review/SKILL.md",
            "skill://review/_manifest",
        }
        assert (await client.read_resource("skill://review/SKILL.md"))[
            0
        ].text == "# Permissive existing skill\n"
        assert (await client.read_resource("skill://review/notes.txt"))[
            0
        ].text == "supporting content"
        assert (
            "notes.txt"
            in (await client.read_resource("skill://review/_manifest"))[0].text
        )
        assert await client.list_skills() == []
        (directory / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review code\n---\n"
        )
        assert len(await client.list_skills()) == 1
