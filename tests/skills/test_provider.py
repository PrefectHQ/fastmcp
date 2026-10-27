from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

import anyio
import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import BlobResourceContents, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.authorization import AuthMiddleware
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills.extension import SkillsExtension
from fastmcp.skills.provider import SkillCatalogProvider
from fastmcp.utilities.skills import list_skill_summaries, sync_skills

REVIEW = Skill(
    name="review",
    description="Review changes",
    instructions="# Review\r\n\r\nRead notes.md first.\r\n",
    files={"notes.md": "line one\r\n", "refs/logo.bin": b"\x00\xff\r\n"},
)
DEPLOY = Skill(name="deploy", description="Deploy the app", instructions="# Deploy\n")


def serve(
    provider: SkillCatalogProvider, middleware: list[Middleware] | None = None
) -> FastMCP:
    server = FastMCP("Skills", providers=[provider], middleware=middleware)
    server.add_extension(SkillsExtension(providers=[provider]))
    return server


def skills_client(server: FastMCP) -> Client:
    return Client(server, mode="auto", extensions=[SkillsClientExtension()])


async def read_bytes(client: Client, uri: str) -> bytes:
    (content,) = await client.read_resource(uri)
    if isinstance(content, TextResourceContents):
        return content.text.encode()
    assert isinstance(content, BlobResourceContents)
    return base64.b64decode(content.blob)


async def test_discovery_round_trip_preserves_bytes():
    server = serve(SkillCatalogProvider({"team/review": REVIEW, "deploy": DEPLOY}))
    async with skills_client(server) as client:
        entries = await client.list_skills()
        assert [entry.uri for entry in entries] == [
            "skill://deploy/SKILL.md",
            "skill://team/review/SKILL.md",
        ]
        for entry in entries:
            assert await client.get_skill(entry.uri) == entry
            assert isinstance(entry.resources, list)
            for record in entry.resources:
                data = await read_bytes(client, record.uri)
                assert f"sha256:{hashlib.sha256(data).hexdigest()}" == record.digest


async def test_resources_work_without_the_extension():
    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])])
    async with Client(server) as client:
        uris = sorted(str(r.uri) for r in await client.list_resources())
        assert uris == [
            "skill://review/SKILL.md",
            "skill://review/_manifest",
            "skill://review/notes.md",
            "skill://review/refs/logo.bin",
        ]
        assert await read_bytes(client, "skill://review/refs/logo.bin") == (
            b"\x00\xff\r\n"
        )
        manifest = json.loads(await read_bytes(client, "skill://review/_manifest"))
        assert {f["path"] for f in manifest["files"]} == set(REVIEW.files)
        summaries = await list_skill_summaries(client)
        assert [(s.name, s.description) for s in summaries] == [
            ("review", "Review changes")
        ]


async def test_sync_skills_installs_code_defined_skills(tmp_path: Path):
    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW, DEPLOY])])
    async with Client(server) as client:
        await sync_skills(client, tmp_path)
    # download_skill writes text files in text mode, so compare file sets and
    # binary bytes rather than exact text bytes (Windows translates newlines).
    for skill in (REVIEW, DEPLOY):
        installed = Skill.from_directory(tmp_path / skill.name)
        assert installed.frontmatter == skill.frontmatter
        assert set(installed.files) == set(skill.files)
    review = Skill.from_directory(tmp_path / "review")
    assert review.files["refs/logo.bin"] == REVIEW.files["refs/logo.bin"]


async def test_load_skills_is_called_once_per_request():
    calls = 0

    class Database(SkillCatalogProvider):
        async def load_skills(self) -> Iterable[Skill]:
            nonlocal calls
            calls += 1
            return [REVIEW, DEPLOY]

    server = serve(Database())
    async with skills_client(server) as client:
        calls = 0
        assert len(await client.list_skills()) == 2
        assert calls == 1


async def test_concurrent_lookups_share_one_load():
    calls = 0

    class Slow(SkillCatalogProvider):
        async def load_skills(self) -> Iterable[Skill]:
            nonlocal calls
            calls += 1
            await anyio.sleep(0.01)
            return [REVIEW]

    server = FastMCP("Skills", providers=[Slow()])

    @server.tool
    async def read_two() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.get_resource, "skill://review/SKILL.md")
            tg.start_soon(server.get_resource, "skill://review/notes.md")

    async with Client(server) as client:
        calls = 0
        await client.call_tool("read_two")
        assert calls == 1


async def test_runtime_catalog_changes_are_visible():
    current = [REVIEW]

    class Dynamic(SkillCatalogProvider):
        async def load_skills(self) -> Iterable[Skill]:
            return list(current)

    server = serve(Dynamic())
    async with skills_client(server) as client:
        assert [e.frontmatter.name for e in await client.list_skills()] == ["review"]
        current.append(DEPLOY)
        assert [e.frontmatter.name for e in await client.list_skills()] == [
            "deploy",
            "review",
        ]


@pytest.mark.parametrize("denied", ["review/SKILL.md", "review/notes.md"])
async def test_denied_file_hides_skill(denied: str):
    blocked = {denied}
    server = serve(
        SkillCatalogProvider([REVIEW]),
        middleware=[AuthMiddleware(auth=lambda ctx: ctx.component.name not in blocked)],
    )
    async with skills_client(server) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")
        blocked.clear()
        assert len(await client.list_skills()) == 1


async def test_shadowed_file_hides_skill():
    server = serve(SkillCatalogProvider([REVIEW]))

    @server.resource("skill://review/notes.md")
    def shadow() -> str:
        return "different bytes"

    async with skills_client(server) as client:
        assert await client.list_skills() == []


@pytest.mark.parametrize(
    "skills, message",
    [
        ([REVIEW, REVIEW], "Duplicate skill name"),
        ({"team/other": REVIEW}, "must end with"),
        ({"review": REVIEW, "review/sub/deploy": DEPLOY}, "nested"),
        ({"../review": REVIEW}, "invalid segment"),
    ],
)
def test_invalid_catalogs(skills: object, message: str):
    with pytest.raises(ValueError, match=message):
        SkillCatalogProvider(skills)  # ty: ignore[invalid-argument-type]


async def test_read_skill_returns_the_served_skill():
    server = serve(SkillCatalogProvider({"team/review": REVIEW, "deploy": DEPLOY}))
    async with skills_client(server) as client:
        assert await client.read_skill("skill://team/review/SKILL.md") == REVIEW
        assert await client.read_skill("skill://deploy/SKILL.md") == DEPLOY


async def test_read_skill_can_be_served_again():
    source = serve(SkillCatalogProvider([REVIEW]))
    async with skills_client(source) as client:
        skill = await client.read_skill("skill://review/SKILL.md")

    mirror = serve(SkillCatalogProvider([skill]))
    async with skills_client(mirror) as client:
        assert await client.read_skill("skill://review/SKILL.md") == REVIEW


async def test_read_skill_rejects_bytes_that_miss_the_manifest():
    provider = SkillCatalogProvider([REVIEW])
    server = serve(provider)

    @server.resource("skill://review/notes.md")
    def tampered() -> str:
        return "tampered\n"

    async with skills_client(server) as client:
        entry = REVIEW.to_mcp_skill()
        client.get_skill = _returning(entry)  # type: ignore[method-assign]
        with pytest.raises(ValueError, match="does not match its manifest"):
            await client.read_skill(entry.uri)


def _returning(entry: object):
    async def get_skill(uri: str, **kwargs: object) -> object:
        return entry

    return get_skill
