from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Literal

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import BlobResourceContents, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills.extension import SkillsExtension


@pytest.mark.parametrize("mode", ["template", "resources"])
async def test_filesystem_skills_round_trip(
    tmp_path: Path, mode: Literal["template", "resources"]
):
    root = tmp_path / "team" / "review"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_bytes(
        b"---\r\nname: review\r\ndescription: Review changes\r\n---\r\n# Review\r\n"
    )
    (root / "notes.md").write_bytes(b"line one\r\nline two\r\n")
    (root / "image.bin").write_bytes(b"\x00\xff\r\n")
    nested = root / "child"
    nested.mkdir()
    (nested / "SKILL.md").write_text(
        "---\nname: child\ndescription: Child skill\n---\n"
    )
    provider = SkillsDirectoryProvider(
        tmp_path, experimental_skills=True, supporting_files=mode
    )
    server = FastMCP("Skills")
    server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        entries = await client.list_skills()
        assert [entry.uri for entry in entries] == [
            "skill://team/review/SKILL.md",
            "skill://team/review/child/SKILL.md",
        ]
        for entry in entries:
            assert await client.get_skill(entry.uri) == entry
            assert isinstance(entry.resources, list)
            for record in entry.resources:
                contents = await client.read_resource(record.uri)
                assert len(contents) == 1
                content = contents[0]
                if isinstance(content, TextResourceContents):
                    data = content.text.encode()
                else:
                    assert isinstance(content, BlobResourceContents)
                    data = base64.b64decode(content.blob)
                assert len(data) == record.size
                assert f"sha256:{hashlib.sha256(data).hexdigest()}" == record.digest


async def test_shadowed_supporting_file_hides_entire_skill(tmp_path: Path):
    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: review\ndescription: Review code\n---\n")
    (root / "notes.md").write_text("original")
    provider = SkillsDirectoryProvider(tmp_path, experimental_skills=True)
    server = FastMCP("Skills")
    server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=[provider]))

    @server.resource("skill://review/notes.md")
    def shadow() -> str:
        return "different bytes"

    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")


async def test_cache_does_not_hide_file_changes(tmp_path: Path):
    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: review\ndescription: Review code\n---\n")
    notes = root / "notes.md"
    notes.write_text("before")
    provider = SkillsDirectoryProvider(tmp_path, experimental_skills=True)
    server = FastMCP("Skills", middleware=[ResponseCachingMiddleware()])
    server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        before = await client.read_resource("skill://review/notes.md")
        assert before[0].text == "before"
        notes.write_text("after")
        entry = await client.get_skill("skill://review/SKILL.md")
        after = await client.read_resource("skill://review/notes.md")
        assert after[0].text == "after"
        assert isinstance(entry.resources, list)
        record = next(
            record for record in entry.resources if record.uri.endswith("notes.md")
        )
        assert record.digest == f"sha256:{hashlib.sha256(b'after').hexdigest()}"


@pytest.mark.parametrize("mode", ["template", "resources"])
async def test_denied_supporting_component_hides_skill(
    tmp_path: Path, mode: Literal["template", "resources"]
):
    class RestrictedProvider(SkillsDirectoryProvider):
        async def _get_resource(self, uri, version=None):
            resource = await super()._get_resource(uri, version)
            if resource is not None and uri.endswith("notes.md"):
                resource.auth = lambda context: False
            return resource

        async def _get_resource_template(self, uri, version=None):
            template = await super()._get_resource_template(uri, version)
            if template is not None:
                template.auth = lambda context: False
            return template

    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: review\ndescription: Review code\n---\n")
    (root / "notes.md").write_text("private")
    provider = RestrictedProvider(
        tmp_path, experimental_skills=True, supporting_files=mode
    )
    server = FastMCP("Skills")
    server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")


async def test_duplicate_and_malformed_skills_leave_unrelated_entries(tmp_path: Path):
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        directory = root / "review"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review code\n---\n"
        )
    good = roots[0] / "other"
    good.mkdir()
    (good / "SKILL.md").write_text(
        "---\nname: other\ndescription: Another skill\n---\n"
    )
    bad = roots[0] / "invalid"
    bad.mkdir()
    (bad / "SKILL.md").write_bytes(b"\xff")
    provider = SkillsDirectoryProvider(roots, experimental_skills=True)
    server = FastMCP("Skills")
    server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        assert [entry.uri for entry in await client.list_skills()] == [
            "skill://other/SKILL.md"
        ]


async def test_denied_main_cannot_fall_back_to_template(tmp_path: Path):
    class RestrictedProvider(SkillsDirectoryProvider):
        async def _get_resource(self, uri, version=None):
            resource = await super()._get_resource(uri, version)
            if resource is not None:
                resource.auth = lambda context: False
            return resource

    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Private skill\n---\nsecret"
    )
    provider = RestrictedProvider(tmp_path, experimental_skills=True)
    server = FastMCP("Skills")
    server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError):
            await client.get_skill("skill://review/SKILL.md")
        for uri in (
            "skill://review/SKILL.md",
            "skill://review/SK%49LL.md",
            "skill://review/%53KILL.md",
            "skill://review/./SKILL.md",
            "skill://review//SKILL.md",
            "skill://review/child/../SKILL.md",
        ):
            with pytest.raises(MCPError):
                await client.read_resource(uri)


@pytest.mark.parametrize("local_duplicate", [False, True])
async def test_duplicate_identities_across_providers_are_omitted(
    tmp_path: Path, local_duplicate: bool
):
    providers = []
    for name in ("one", "two"):
        root = tmp_path / name / "review"
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(f"---\nname: review\ndescription: {name}\n---\n")
        providers.append(SkillsDirectoryProvider(root.parent, experimental_skills=True))
    if local_duplicate:
        providers[0] = SkillsDirectoryProvider(
            [tmp_path / "one", tmp_path / "one"], experimental_skills=True
        )
    server = FastMCP("Skills")
    for provider in providers:
        server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=providers))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        assert await client.list_skills() == []
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")


@pytest.mark.parametrize("mode", ["template", "resources"])
@pytest.mark.parametrize("deny_main", [False, True])
async def test_global_resource_authorization_hides_skill(
    tmp_path: Path, mode: Literal["template", "resources"], deny_main: bool
):
    from fastmcp.server.middleware.authorization import AuthMiddleware

    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: review\ndescription: Private instructions\n---\n"
    )
    (root / "notes.md").write_text("private supporting file")
    denied = {"review/SKILL.md"} if deny_main else {"review/notes.md", "review_files"}
    provider = SkillsDirectoryProvider(
        tmp_path, supporting_files=mode, experimental_skills=True
    )
    server = FastMCP(
        "Skills",
        providers=[provider],
        middleware=[AuthMiddleware(auth=lambda ctx: ctx.component.name not in denied)],
    )
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        with pytest.raises(MCPError):
            await client.read_resource(
                "skill://review/SKILL.md" if deny_main else "skill://review/notes.md"
            )
        assert await client.list_skills() == []
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")
        denied.clear()
        assert len(await client.list_skills()) == 1
        assert (
            await client.get_skill("skill://review/SKILL.md")
        ).frontmatter.name == "review"


@pytest.mark.parametrize("mode", ["template", "resources"])
async def test_shared_access_policy_controls_reads_and_skill_metadata(
    tmp_path: Path, mode
):
    blocked = {"skill://review/notes.md"}

    class ResourcePolicy(Middleware):
        async def check_resource_access(self, context):
            if str(context.message.uri) in blocked:
                raise AuthorizationError("Private supporting file")

    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: review\ndescription: Review code\n---\n")
    (root / "notes.md").write_text("private")
    provider = SkillsDirectoryProvider(
        tmp_path, experimental_skills=True, supporting_files=mode
    )
    server = FastMCP("Skills", providers=[provider], middleware=[ResourcePolicy()])
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        with pytest.raises(MCPError, match="Private supporting file"):
            await client.read_resource("skill://review/notes.md")
        assert await client.list_skills() == []
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://review/SKILL.md")
        blocked.clear()
        assert (await client.read_resource("skill://review/notes.md"))[
            0
        ].text == "private"
        assert len(await client.list_skills()) == 1
        assert (
            await client.get_skill("skill://review/SKILL.md")
        ).frontmatter.name == "review"
