"""Resource-based skill discovery distinguishes skills from supporting files."""

from pathlib import Path
from typing import Literal

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import (
    SkillsDirectoryProvider as LegacyDirectoryProvider,
)
from fastmcp.skills import Skill
from fastmcp.skills.provider import SkillCatalogProvider, SkillsDirectoryProvider
from fastmcp.utilities.skills import list_skills, sync_skills

pytestmark = pytest.mark.filterwarnings(
    "ignore::fastmcp._warnings.FastMCPDeprecationWarning"
)

REVIEW = Skill(
    name="review",
    description="Review changes",
    instructions="Read the examples first.",
    files={"examples/SKILL.md": "sample", "examples/_manifest": "example"},
)


@pytest.mark.parametrize("provider", ["catalog", "directory"])
@pytest.mark.parametrize("prefix", ["", "team/"])
async def test_supporting_skill_files_are_not_discovered(
    tmp_path: Path, provider: str, prefix: str
) -> None:
    path = f"{prefix}review"
    if provider == "catalog":
        server = FastMCP("Skills", providers=[SkillCatalogProvider({path: REVIEW})])
    else:
        REVIEW.to_directory(tmp_path / path)
        server = FastMCP("Skills", providers=[SkillsDirectoryProvider(tmp_path)])

    async with Client(server) as client:
        resources = await client.list_skill_resources()
        assert [str(resource.uri) for resource in resources] == [
            f"skill://{path}/SKILL.md"
        ]
        assert await client.read_skill(str(resources[0].uri)) == REVIEW
        assert [summary.uri for summary in await list_skills(client)] == [
            f"skill://{path}/SKILL.md"
        ]


@pytest.mark.parametrize("supporting_files", ["template", "resources"])
async def test_discovery_on_a_legacy_provider(
    tmp_path: Path, supporting_files: Literal["template", "resources"]
) -> None:
    REVIEW.to_directory(tmp_path / "review")
    server = FastMCP(
        "Legacy",
        providers=[
            LegacyDirectoryProvider(tmp_path, supporting_files=supporting_files)
        ],
    )
    async with Client(server) as client:
        resources = await client.list_skill_resources()
        assert [str(resource.uri) for resource in resources] == [
            "skill://review/SKILL.md"
        ]
        assert await client.read_skill(str(resources[0].uri)) == REVIEW


async def test_sync_installs_supporting_skill_files(tmp_path: Path) -> None:
    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])])
    async with Client(server) as client:
        assert [summary.name for summary in await list_skills(client)] == ["review"]
        assert await sync_skills(client, tmp_path) == [tmp_path / "review"]
    assert Skill.from_directory(tmp_path / "review") == REVIEW


@pytest.mark.parametrize("mounted", [False, True])
async def test_independent_skills_can_share_a_path_prefix(mounted: bool) -> None:
    deploy = Skill(name="deploy", description="Deploy", instructions="Deploy the app.")
    server = FastMCP("Parent", providers=[SkillCatalogProvider([REVIEW])])
    if mounted:
        server.mount(
            FastMCP("Child", providers=[SkillCatalogProvider([deploy])]),
            namespace="review",
        )
    else:
        server.add_provider(SkillCatalogProvider({"review/deploy": deploy}))

    async with Client(server) as client:
        resources = await client.list_skill_resources()
        assert {str(resource.uri) for resource in resources} == {
            "skill://review/SKILL.md",
            "skill://review/deploy/SKILL.md",
        }
        assert await client.read_skill("skill://review/SKILL.md") == REVIEW
        assert await client.read_skill("skill://review/deploy/SKILL.md") == deploy


async def test_manifest_free_resources_keep_their_order_and_metadata() -> None:
    server = FastMCP("Manual")
    reads = 0

    @server.resource("skill://team/review/nested/SKILL.md", description="Nested")
    def nested() -> str:
        nonlocal reads
        reads += 1
        raise AssertionError("Discovery should not read skill documents")

    @server.resource("skill://team/review/SKILL.md", description="Review")
    def review() -> str:
        nonlocal reads
        reads += 1
        raise AssertionError("Discovery should not read skill documents")

    @server.resource("file:///tmp/SKILL.md")
    def other_scheme() -> str:
        return "# Not a skill resource"

    @server.resource("skill://team/review/notes.md")
    def notes() -> str:
        return "# Notes"

    async with Client(server) as client:
        all_resources = await client.list_resources()
        assert await client.list_skill_resources() == all_resources[:2]
        assert [summary.description for summary in await list_skills(client)] == [
            "Nested",
            "Review",
        ]
    assert reads == 0


@pytest.mark.parametrize("unreadable", [False, True])
async def test_unusable_manifest_keeps_candidates(unreadable: bool) -> None:
    server = FastMCP("Manual")
    reads = 0

    @server.resource("skill://review/SKILL.md")
    def review() -> str:
        return "# Review"

    @server.resource("skill://review/nested/SKILL.md")
    def nested() -> str:
        return "# Nested"

    @server.resource("skill://review/_manifest")
    def manifest() -> str:
        nonlocal reads
        reads += 1
        if unreadable:
            raise ValueError("Manifest is unavailable")
        return "not JSON"

    async with Client(server) as client:
        resources = await client.list_skill_resources()
    assert [str(resource.uri) for resource in resources] == [
        "skill://review/SKILL.md",
        "skill://review/nested/SKILL.md",
    ]
    assert reads == 1


async def test_unambiguous_skills_do_not_need_manifest_reads() -> None:
    server = FastMCP("Manual")
    reads = 0

    @server.resource("skill://review/SKILL.md")
    def review() -> str:
        return "# Review"

    @server.resource("skill://review/_manifest")
    def manifest() -> str:
        nonlocal reads
        reads += 1
        raise AssertionError("Unambiguous discovery should not read manifests")

    async with Client(server) as client:
        (resource,) = await client.list_skill_resources()
    assert str(resource.uri) == "skill://review/SKILL.md"
    assert reads == 0
