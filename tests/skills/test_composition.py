from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import BlobResourceContents, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.server.middleware.authorization import AuthMiddleware
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.server.transforms import Namespace
from fastmcp.skills import MCPSkill, Skill, SkillsClientExtension
from fastmcp.skills.provider import SkillCatalogProvider

REVIEW = Skill(
    name="review",
    description="Review changes",
    instructions="# Review\n\nRead notes.md first.\n",
    files={"notes.md": "line one\n"},
)


def child(*skills: Skill) -> FastMCP:
    return FastMCP("Child", providers=[SkillCatalogProvider(list(skills) or [REVIEW])])


def skills_client(server: FastMCP) -> Client:
    return Client(server, extensions=[SkillsClientExtension()])


async def assert_files_match(client: Client, entry: MCPSkill) -> None:
    assert isinstance(entry.resources, list)
    for record in entry.resources:
        (content,) = await client.read_resource(record.uri)
        if isinstance(content, TextResourceContents):
            data = content.text.encode()
        else:
            assert isinstance(content, BlobResourceContents)
            data = base64.b64decode(content.blob)
        assert f"sha256:{hashlib.sha256(data).hexdigest()}" == record.digest


async def test_provider_registers_the_extension():
    async with skills_client(child()) as client:
        assert [e.uri for e in await client.list_skills()] == [
            "skill://review/SKILL.md"
        ]


async def test_mounted_skills_are_listed_by_the_parent():
    parent = FastMCP("Parent")
    parent.mount(child())
    async with skills_client(parent) as client:
        (entry,) = await client.list_skills()
        assert entry.uri == "skill://review/SKILL.md"
        assert await client.get_skill(entry.uri) == entry
        await assert_files_match(client, entry)


async def test_namespaces_rewrite_every_uri():
    inner = FastMCP("Inner")
    inner.mount(child(), namespace="team")
    parent = FastMCP("Parent")
    parent.mount(inner, namespace="org")
    async with skills_client(parent) as client:
        (entry,) = await client.list_skills()
        assert entry.uri == "skill://org/team/review/SKILL.md"
        assert isinstance(entry.resources, list)
        assert {r.uri for r in entry.resources} == {
            "skill://org/team/review/SKILL.md",
            "skill://org/team/review/notes.md",
        }
        assert await client.get_skill(entry.uri) == entry
        await assert_files_match(client, entry)
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")


async def test_mounted_folder_skills_are_listed(tmp_path: Path):
    folder = tmp_path / "skills" / "deploy"
    (folder / "refs").mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(
        b"---\nname: deploy\ndescription: Deploy the app\n---\n# Deploy\n"
    )
    (folder / "refs" / "checklist.md").write_bytes(b"- tests\n")
    folders = FastMCP(
        "Folders",
        providers=[
            SkillsDirectoryProvider(tmp_path / "skills", experimental_skills=True)
        ],
    )
    parent = FastMCP("Parent")
    parent.mount(folders, namespace="ops")
    async with skills_client(parent) as client:
        (entry,) = await client.list_skills()
        assert entry.uri == "skill://ops/deploy/SKILL.md"
        await assert_files_match(client, entry)


async def test_parent_access_rules_hide_mounted_skills():
    blocked = {"skill://team/review/notes.md"}
    parent = FastMCP(
        "Parent",
        middleware=[
            AuthMiddleware(auth=lambda ctx: str(ctx.component.uri) not in blocked)
        ],
    )
    parent.mount(child(), namespace="team")
    async with skills_client(parent) as client:
        assert await client.list_skills() == []
        blocked.clear()
        assert len(await client.list_skills()) == 1


async def test_parent_resource_shadowing_hides_mounted_skill():
    parent = FastMCP("Parent")

    @parent.resource("skill://review/notes.md")
    def shadow() -> str:
        return "different bytes"

    parent.mount(child())
    async with skills_client(parent) as client:
        assert await client.list_skills() == []


async def test_colliding_mounts_omit_the_skill():
    parent = FastMCP("Parent")
    parent.mount(child())
    parent.mount(child())
    async with skills_client(parent) as client:
        assert await client.list_skills() == []


async def test_server_transforms_disable_discovery_without_failing_mount():
    parent = FastMCP("Parent")
    parent.add_transform(Namespace("all"))
    parent.mount(child())
    async with skills_client(parent) as client:
        assert await client.list_skills() == []
        assert len(await client.list_resources()) == 3


async def test_hidden_parent_resource_hides_mounted_template_file(tmp_path: Path):
    folder = tmp_path / "skills" / "deploy"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(
        b"---\nname: deploy\ndescription: Deploy the app\n---\n# Deploy\n"
    )
    (folder / "notes.md").write_bytes(b"- tests\n")
    folders = FastMCP(
        "Folders",
        providers=[
            SkillsDirectoryProvider(tmp_path / "skills", experimental_skills=True)
        ],
    )
    parent = FastMCP("Parent")

    @parent.resource("skill://deploy/notes.md", auth=lambda ctx: False)
    def hidden() -> str:
        return "secret"

    parent.mount(folders)
    async with skills_client(parent) as client:
        assert await client.list_skills() == []
