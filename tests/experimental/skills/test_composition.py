"""Local skills behind transforms and mounted servers."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp import MCPError
from mcp_types import INVALID_PARAMS

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.server.transforms import Namespace, ResourcesAsTools, Visibility
from fastmcp.server.transforms.version_filter import VersionFilter
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.extension import SkillsExtension


def write_skills(root: Path, *names: str) -> SkillsDirectoryProvider:
    for name in names:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_bytes(
            f"---\nname: {name}\ndescription: Use {name}.\n---\n\n# {name}\n".encode()
        )
        (directory / "guide.txt").write_bytes(b"Supporting instructions\n")
    return SkillsDirectoryProvider(root, experimental_skills=True)


def skills_server(provider: SkillsDirectoryProvider, **kwargs) -> FastMCP:
    server = FastMCP("skills", providers=[provider], **kwargs)
    server.add_extension(SkillsExtension(providers=[provider]))
    return server


def skills_client(server: FastMCP) -> Client:
    return Client(server, mode="auto", extensions=[SkillsClientExtension()])


async def assert_serves(server: FastMCP, *uris: str) -> None:
    async with skills_client(server) as client:
        skills = await client.list_skills()
        assert [skill.uri for skill in skills] == list(uris)
        for skill in skills:
            assert await client.get_skill(skill.uri) == skill
            assert isinstance(skill.resources, list)
            for resource in skill.resources:
                contents = await client.read_resource(resource.uri)
                assert len(contents) == 1


async def test_provider_namespace_rewrites_entries(tmp_path: Path) -> None:
    provider = write_skills(tmp_path, "review")
    provider.add_transform(Namespace("docs"))
    server = skills_server(provider)

    await assert_serves(server, "skill://docs/review/SKILL.md")
    async with skills_client(server) as client:
        skill = await client.get_skill("skill://docs/review/SKILL.md")
        assert isinstance(skill.resources, list)
        assert sorted(resource.uri for resource in skill.resources) == [
            "skill://docs/review/SKILL.md",
            "skill://docs/review/guide.txt",
        ]
        with pytest.raises(MCPError) as error:
            await client.get_skill("skill://review/SKILL.md")
        assert error.value.error.code == INVALID_PARAMS


async def test_server_namespace_rewrites_entries(tmp_path: Path) -> None:
    provider = write_skills(tmp_path, "review")
    server = skills_server(provider, transforms=[Namespace("docs")])

    await assert_serves(server, "skill://docs/review/SKILL.md")


async def test_visibility_hides_skill_with_hidden_file(tmp_path: Path) -> None:
    provider = write_skills(tmp_path, "review", "deploy")
    provider.add_transform(Visibility(False, keys={"template:skill://deploy/{path*}@"}))
    server = skills_server(provider)

    await assert_serves(server, "skill://review/SKILL.md")


async def test_version_filter_hides_unversioned_skills(tmp_path: Path) -> None:
    provider = write_skills(tmp_path, "review")
    provider.add_transform(VersionFilter(version_gte="1", include_unversioned=False))
    server = skills_server(provider)

    await assert_serves(server)


async def test_resource_neutral_transform_keeps_skills(tmp_path: Path) -> None:
    provider = write_skills(tmp_path, "review")
    server = skills_server(provider)
    server.add_transform(ResourcesAsTools(server))

    await assert_serves(server, "skill://review/SKILL.md")


@pytest.mark.parametrize("namespace", [None, "team"])
async def test_mounted_server_forwards_local_skills(
    tmp_path: Path, namespace: str | None
) -> None:
    child = skills_server(write_skills(tmp_path, "review"))
    parent = FastMCP("parent")
    parent.mount(child, namespace=namespace)
    prefix = f"{namespace}/" if namespace else ""

    async with skills_client(parent) as client:
        assert client.server_capabilities is not None
        assert SKILLS_EXTENSION_ID in (client.server_capabilities.extensions or {})
    await assert_serves(parent, f"skill://{prefix}review/SKILL.md")


async def test_mounted_server_without_extension_is_not_forwarded(
    tmp_path: Path,
) -> None:
    child = FastMCP("child", providers=[write_skills(tmp_path, "review")])
    parent = FastMCP("parent")
    parent.mount(child, namespace="team")

    async with skills_client(parent) as client:
        assert client.server_capabilities is not None
        assert SKILLS_EXTENSION_ID not in (client.server_capabilities.extensions or {})


async def test_parent_and_mounted_skills_share_one_catalog(tmp_path: Path) -> None:
    child = skills_server(write_skills(tmp_path / "child", "review"))
    parent = skills_server(write_skills(tmp_path / "parent", "deploy"))
    parent.mount(child, namespace="team")

    await assert_serves(
        parent, "skill://deploy/SKILL.md", "skill://team/review/SKILL.md"
    )
