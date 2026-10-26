"""Public skill utilities preserve directory names across URI boundaries."""

import json
from pathlib import Path
from typing import Literal

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.utilities.skills import (
    download_skill,
    get_skill_manifest,
    list_skills,
    sync_skills,
)

SKILL_HOSTS = {
    "plain-skill": "plain-skill",
    "café": "caf%C3%A9",
    "技能": "%E6%8A%80%E8%83%BD",
    "my skill": "my%20skill",
    "my%20skill": "my%2520skill",
    "skill#one": "skill%23one",
    "skill+one": "skill%2Bone",
}


@pytest.fixture(params=["template", "resources"])
def named_skills_server(tmp_path: Path, request: pytest.FixtureRequest) -> FastMCP:
    roots = tmp_path / "source"
    for name in SKILL_HOSTS:
        skill = roots / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}", encoding="utf-8")
        (skill / "guide.md").write_text(f"Guide for {name}", encoding="utf-8")
        (skill / "data.bin").write_bytes(b"\x00\xff")
        (skill / "notes #1.md").write_text(f"Notes for {name}", encoding="utf-8")
        (skill / "rate%20.md").write_text(
            f"Literal percent for {name}", encoding="utf-8"
        )
    server = FastMCP("Named skills")
    supporting_files: Literal["template", "resources"] = request.param
    server.add_provider(
        SkillsDirectoryProvider(roots=roots, supporting_files=supporting_files)
    )
    return server


async def test_list_skills_preserves_names(named_skills_server: FastMCP) -> None:
    async with Client(named_skills_server) as client:
        skills = await list_skills(client)
    assert {skill.name: skill.uri for skill in skills} == {
        name: f"skill://{host}/SKILL.md" for name, host in SKILL_HOSTS.items()
    }


async def test_manifest_accepts_directory_names(named_skills_server: FastMCP) -> None:
    async with Client(named_skills_server) as client:
        for name in SKILL_HOSTS:
            manifest = await get_skill_manifest(client, name)
            assert manifest.name == name
            assert {file.path for file in manifest.files} == {
                "SKILL.md",
                "guide.md",
                "data.bin",
                "notes #1.md",
                "rate%20.md",
            }


async def test_download_preserves_names_and_contents(
    named_skills_server: FastMCP, tmp_path: Path
) -> None:
    target = tmp_path / "downloaded"
    async with Client(named_skills_server) as client:
        for name in SKILL_HOSTS:
            path = await download_skill(client, name, target)
            assert path == target / name
            assert (path / "SKILL.md").read_text(encoding="utf-8") == f"# {name}"
            assert (path / "guide.md").read_text(
                encoding="utf-8"
            ) == f"Guide for {name}"
            assert (path / "data.bin").read_bytes() == b"\x00\xff"
            assert (path / "notes #1.md").read_text(
                encoding="utf-8"
            ) == f"Notes for {name}"
            assert (path / "rate%20.md").read_text(encoding="utf-8") == (
                f"Literal percent for {name}"
            )


async def test_sync_preserves_distinct_directory_names(
    named_skills_server: FastMCP, tmp_path: Path
) -> None:
    target = tmp_path / "synced"
    async with Client(named_skills_server) as client:
        paths = await sync_skills(client, target)
    assert set(paths) == {target / name for name in SKILL_HOSTS}
    assert {path.name for path in target.iterdir()} == set(SKILL_HOSTS)
    for path in paths:
        assert (path / "SKILL.md").read_text(encoding="utf-8") == f"# {path.name}"
        assert (path / "guide.md").read_text(
            encoding="utf-8"
        ) == f"Guide for {path.name}"
        assert (path / "data.bin").read_bytes() == b"\x00\xff"
        assert (path / "notes #1.md").read_text(encoding="utf-8") == (
            f"Notes for {path.name}"
        )
        assert (path / "rate%20.md").read_text(encoding="utf-8") == (
            f"Literal percent for {path.name}"
        )


async def test_existing_namespaced_skill_download(tmp_path: Path) -> None:
    server = FastMCP("Namespaced skills")

    @server.resource("skill://team/pdf/SKILL.md")
    def main_file() -> str:
        return "# Team PDF"

    @server.resource("skill://team/pdf/_manifest")
    def manifest() -> str:
        return json.dumps(
            {
                "skill": "team/pdf",
                "files": [{"path": "SKILL.md", "size": 10, "hash": "unused"}],
            }
        )

    async with Client(server) as client:
        summaries = await list_skills(client)
        assert [skill.name for skill in summaries] == ["team/pdf"]
        path = await download_skill(client, "team/pdf", tmp_path)
    assert path == tmp_path / "team" / "pdf"
    assert (path / "SKILL.md").read_text(encoding="utf-8") == "# Team PDF"
