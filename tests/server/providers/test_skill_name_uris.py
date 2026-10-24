"""Skill directory names must round-trip through advertised resource URIs."""

import json
from pathlib import Path
from typing import Literal

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider


@pytest.mark.parametrize("supporting_files", ["template", "resources"])
@pytest.mark.parametrize(
    ("name", "host"),
    [
        ("plain-skill", "plain-skill"),
        ("café", "caf%C3%A9"),
        ("技能", "%E6%8A%80%E8%83%BD"),
        ("my skill", "my%20skill"),
        ("my%20skill", "my%2520skill"),
        ("skill#one", "skill%23one"),
    ],
)
async def test_skill_directory_name_round_trip(
    tmp_path: Path,
    name: str,
    host: str,
    supporting_files: Literal["template", "resources"],
) -> None:
    skill = tmp_path / name
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Main skill", encoding="utf-8")
    (skill / "guide.md").write_text("Supporting content", encoding="utf-8")
    server = FastMCP("Skill names")
    server.add_provider(
        SkillsDirectoryProvider(roots=tmp_path, supporting_files=supporting_files)
    )

    async with Client(server) as client:
        resources = {
            resource.name: resource.uri for resource in await client.list_resources()
        }
        assert str(resources[f"{name}/SKILL.md"]) == f"skill://{host}/SKILL.md"
        assert str(resources[f"{name}/_manifest"]) == f"skill://{host}/_manifest"
        main = await client.read_resource(resources[f"{name}/SKILL.md"])
        assert main[0].text == "# Main skill"
        manifest = await client.read_resource(resources[f"{name}/_manifest"])
        assert json.loads(manifest[0].text)["skill"] == name

        templates = await client.list_resource_templates()
        if supporting_files == "template":
            assert [template.uri_template for template in templates] == [
                f"skill://{host}/{{path*}}"
            ]
            uri = f"skill://{host}/guide.md"
        else:
            assert templates == []
            uri = resources[f"{name}/guide.md"]
            assert str(uri) == f"skill://{host}/guide.md"
        supporting = await client.read_resource(uri)
        assert supporting[0].text == "Supporting content"


async def test_existing_unescaped_skill_name_template_uri(tmp_path: Path) -> None:
    skill = tmp_path / "skill+one"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill", encoding="utf-8")
    (skill / "guide.md").write_text("Legacy URI content", encoding="utf-8")
    server = FastMCP("Legacy skill URI")
    server.add_provider(SkillsDirectoryProvider(roots=tmp_path))

    async with Client(server) as client:
        result = await client.read_resource("skill://skill+one/guide.md")
        assert result[0].text == "Legacy URI content"
