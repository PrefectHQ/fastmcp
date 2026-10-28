"""Tests for `Client.read_skill`, which fetches and verifies a whole skill."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR

from fastmcp import Client, ClientGroup, FastMCP
from fastmcp.server.providers.skills import SkillProvider, SkillsDirectoryProvider
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills._constants import MAX_SKILL_BYTES, MAX_SKILL_FILES
from fastmcp.skills.extension import SkillsExtension
from fastmcp.skills.models import MCPSkill, SkillFrontmatter, SkillResource
from fastmcp.skills.provider import SkillCatalogProvider

REVIEW = Skill(
    name="review",
    description="Review changes",
    instructions="# Review\r\n\r\nRead notes.md first.\r\n",
    files={"notes.md": "line one\r\n", "refs/logo.bin": b"\x00\xff\r\n"},
)


def skills_client(server: FastMCP) -> Client:
    return Client(server, extensions=[SkillsClientExtension()])


async def test_read_skill_returns_an_equal_skill_through_the_extension():
    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])])
    async with skills_client(server) as client:
        skill = await client.read_skill("skill://review/SKILL.md")
    assert skill == REVIEW
    assert skill.files["refs/logo.bin"] == b"\x00\xff\r\n"


@pytest.mark.parametrize(
    "advertised",
    [
        {"description": "Different instructions"},
        {"custom": {"enabled": 1}},
        {"custom": {"enabled": True, "extra": "claim"}},
    ],
)
async def test_read_skill_reconciles_all_advertised_frontmatter(
    advertised: dict[str, Any],
):
    original = Skill(
        name="review",
        description="Review changes",
        instructions="# Review\n",
        frontmatter={"custom": {"enabled": True}},
    )
    requests = 0

    class Inconsistent(SkillCatalogProvider):
        async def _get_skill_entry(
            self, uri: str, context: ServerRequestContext[Any, Any]
        ) -> MCPSkill | None:
            nonlocal requests
            requests += 1
            entry = await super()._get_skill_entry(uri, context)
            assert entry is not None
            frontmatter = SkillFrontmatter.model_validate(
                {**entry.frontmatter.root, **advertised}
            )
            return entry.model_copy(update={"frontmatter": frontmatter})

    server = FastMCP("Skills", providers=[Inconsistent([original])])
    async with skills_client(server) as client:
        with pytest.raises(ValueError, match="frontmatter"):
            await client.read_skill("skill://review/SKILL.md")
    assert requests == 1


async def test_read_skill_falls_back_to_the_manifest_resource():
    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])])
    async with Client(server) as client:
        assert client._skills_unavailable_reason() is not None
        skill = await client.read_skill("skill://review/SKILL.md")
    assert skill == REVIEW


async def test_read_skill_handles_nested_paths_and_encoded_names():
    quoted = Skill(
        name="deploy",
        description="Deploy",
        instructions="# Deploy\n",
        files={"notes/read me.md": "spaces\n"},
    )
    provider = SkillCatalogProvider({"team/deploy": quoted})
    server = FastMCP("Skills", providers=[provider])
    for client in (skills_client(server), Client(server)):
        async with client:
            skill = await client.read_skill("skill://team/deploy/SKILL.md")
        assert skill == quoted


async def test_read_skill_reads_from_a_legacy_folder_provider(tmp_path: Path):
    folder = tmp_path / "review"
    folder.mkdir()
    (folder / "SKILL.md").write_bytes(REVIEW.files["SKILL.md"])
    (folder / "notes.md").write_bytes(b"line one\r\n")
    (folder / "refs").mkdir()
    (folder / "refs" / "logo.bin").write_bytes(b"\x00\xff\r\n")

    for provider in (
        SkillProvider(folder),
        SkillProvider(folder, supporting_files="resources"),
        SkillsDirectoryProvider(tmp_path),
        SkillsDirectoryProvider(tmp_path, experimental_skills=True),
    ):
        server = FastMCP("Skills", providers=[provider])
        async with Client(server) as client:
            skill = await client.read_skill("skill://review/SKILL.md")
        assert skill == REVIEW, provider


async def test_read_skill_rejects_a_file_that_does_not_match_its_digest():
    class Tampered(SkillCatalogProvider):
        async def _get_skill_entry(
            self, uri: str, context: ServerRequestContext[Any, Any]
        ) -> MCPSkill | None:
            entry = await super()._get_skill_entry(uri, context)
            assert entry is not None and isinstance(entry.resources, list)
            resources = [
                record.model_copy(update={"digest": f"sha256:{'0' * 64}"})
                if record.uri.endswith("/notes.md")
                else record
                for record in entry.resources
            ]
            return entry.model_copy(update={"resources": resources})

    server = FastMCP("Skills", providers=[Tampered([REVIEW])])
    async with skills_client(server) as client:
        with pytest.raises(ValueError, match="'notes.md' does not match"):
            await client.read_skill("skill://review/SKILL.md")


async def test_read_skill_rejects_a_file_whose_size_changed():
    class Tampered(SkillCatalogProvider):
        async def _get_skill_entry(
            self, uri: str, context: ServerRequestContext[Any, Any]
        ) -> MCPSkill | None:
            entry = await super()._get_skill_entry(uri, context)
            assert entry is not None and isinstance(entry.resources, list)
            resources = [
                record.model_copy(update={"size": record.size + 1})
                for record in entry.resources
            ]
            return entry.model_copy(update={"resources": resources})

    server = FastMCP("Skills", providers=[Tampered([REVIEW])])
    async with skills_client(server) as client:
        with pytest.raises(ValueError, match="the manifest promised"):
            await client.read_skill("skill://review/SKILL.md")


def manifest_server(files: dict[str, bytes], manifest: dict[str, Any]) -> FastMCP:
    server = FastMCP("Manual")

    @server.resource("skill://manual/_manifest", mime_type="application/json")
    def _manifest() -> str:
        return json.dumps(manifest)

    for path, content in files.items():
        server.resource(f"skill://manual/{path}")(_constant(content))
    return server


def _constant(content: bytes) -> Callable[[], bytes]:
    def _file() -> bytes:
        return content

    return _file


def manifest_for(files: dict[str, bytes]) -> dict[str, Any]:
    return {
        "skill": "manual",
        "files": [
            {
                "path": path,
                "size": len(content),
                "hash": f"sha256:{hashlib.sha256(content).hexdigest()}",
            }
            for path, content in files.items()
        ],
    }


MANUAL_SKILL_MD = b"---\nname: manual\ndescription: Hand-rolled.\n---\n# Manual\n"


async def test_read_skill_verifies_resource_mode_manifests():
    files = {"SKILL.md": MANUAL_SKILL_MD, "data.bin": b"\x00\x01binary"}
    async with Client(manifest_server(files, manifest_for(files))) as client:
        skill = await client.read_skill("skill://manual/SKILL.md")
    assert dict(skill.files) == files

    tampered = manifest_for(files)
    tampered["files"][1]["hash"] = f"sha256:{'0' * 64}"
    async with Client(manifest_server(files, tampered)) as client:
        with pytest.raises(ValueError, match="'data.bin' does not match"):
            await client.read_skill("skill://manual/SKILL.md")


async def test_read_skill_falls_back_when_the_extension_does_not_know_the_skill():
    files = {"SKILL.md": MANUAL_SKILL_MD}
    server = manifest_server(files, manifest_for(files))
    server.add_provider(SkillCatalogProvider([REVIEW]))
    async with skills_client(server) as client:
        assert [entry.uri for entry in await client.list_skills()] == [
            "skill://review/SKILL.md"
        ]
        skill = await client.read_skill("skill://manual/SKILL.md")
    assert dict(skill.files) == files


@pytest.mark.parametrize(
    ("manifest", "match"),
    [
        ({"skill": "manual"}, "Invalid skill manifest"),
        ({"skill": "manual", "files": []}, "lists no files"),
        (
            {"skill": "manual", "files": [{"path": "SKILL.md", "size": "x"}]},
            "Invalid skill manifest",
        ),
    ],
)
async def test_read_skill_rejects_unusable_manifests(
    manifest: dict[str, Any], match: str
):
    files = {"SKILL.md": MANUAL_SKILL_MD}
    async with Client(manifest_server(files, manifest)) as client:
        with pytest.raises(ValueError, match=match):
            await client.read_skill("skill://manual/SKILL.md")


async def test_read_skill_rejects_manifest_paths_that_escape_the_skill():
    files = {"SKILL.md": MANUAL_SKILL_MD}
    manifest = manifest_for(files)
    escape = b"owned"
    manifest["files"].append(
        {
            "path": "../escape.txt",
            "size": len(escape),
            "hash": f"sha256:{hashlib.sha256(escape).hexdigest()}",
        }
    )
    server = manifest_server(files, manifest)
    requested: list[str] = []

    @server.resource("skill://escape.txt")
    def _escape() -> bytes:
        requested.append("escape")
        return escape

    async with Client(server) as client:
        with pytest.raises(ValueError, match="invalid segment"):
            await client.read_skill("skill://manual/SKILL.md")
    assert requested == []


async def test_read_skill_requires_a_skill_that_matches_its_uri():
    files = {"SKILL.md": MANUAL_SKILL_MD}
    server = manifest_server(files, manifest_for(files))

    @server.resource("skill://other/_manifest", mime_type="application/json")
    def _other_manifest() -> str:
        return json.dumps(manifest_for(files))

    @server.resource("skill://other/SKILL.md")
    def _other_skill() -> bytes:
        return MANUAL_SKILL_MD

    async with Client(server) as client:
        with pytest.raises(ValueError, match="is named 'manual'"):
            await client.read_skill("skill://other/SKILL.md")


async def test_read_skill_takes_a_skill_md_uri():
    server = FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])])
    async with Client(server) as client:
        with pytest.raises(ValueError, match="SKILL.md URI"):
            await client.read_skill("skill://review/notes.md")


async def test_read_skill_requires_a_connection():
    client = Client(FastMCP("Skills", providers=[SkillCatalogProvider([REVIEW])]))
    with pytest.raises(RuntimeError, match="not connected"):
        await client.read_skill("skill://review/SKILL.md")


@pytest.mark.parametrize("limit", ["count", "size"])
async def test_read_skill_rejects_oversized_entries_before_reading_files(limit: str):
    original = Skill(name="review", description="Review", instructions="# Review\n")
    reads = 0

    class Oversized(SkillCatalogProvider):
        async def _get_skill_entry(
            self, uri: str, context: ServerRequestContext[Any, Any]
        ) -> MCPSkill | None:
            entry = original.to_mcp_skill()
            assert isinstance(entry.resources, list)
            records = entry.resources
            if limit == "count":
                records.extend(
                    SkillResource(
                        uri=f"skill://review/{index}.txt",
                        digest=f"sha256:{hashlib.sha256(b'').hexdigest()}",
                        size=0,
                    )
                    for index in range(MAX_SKILL_FILES)
                )
            else:
                records[0] = records[0].model_copy(update={"size": MAX_SKILL_BYTES + 1})
            return entry.model_copy(update={"resources": records})

    server = FastMCP("Skills", providers=[Oversized([original])])

    @server.resource("skill://review/SKILL.md")
    def observed_file() -> bytes:
        nonlocal reads
        reads += 1
        return original.files["SKILL.md"]

    async with skills_client(server) as client:
        with pytest.raises(ValueError, match="at most"):
            await client.read_skill("skill://review/SKILL.md")
    assert reads == 0


async def test_a_read_skill_can_be_served_again():
    origin = FastMCP(
        "Origin", providers=[SkillCatalogProvider({"team/review": REVIEW})]
    )
    async with skills_client(origin) as client:
        copied = await client.read_skill("skill://team/review/SKILL.md")

    mirror = FastMCP("Mirror", providers=[SkillCatalogProvider([copied])])
    async with skills_client(mirror) as client:
        entries = await client.list_skills()
        assert [entry.uri for entry in entries] == ["skill://review/SKILL.md"]
        assert await client.read_skill("skill://review/SKILL.md") == REVIEW


async def test_client_group_read_skill_uses_the_named_server():
    group = ClientGroup(
        {
            "a": skills_client(
                FastMCP("a", providers=[SkillCatalogProvider([REVIEW])])
            ),
            "b": Client(FastMCP("b")),
        }
    )
    async with group:
        assert await group.read_skill("a", "skill://review/SKILL.md") == REVIEW
        with pytest.raises(KeyError, match="Unknown ClientGroup server"):
            await group.read_skill("c", "skill://review/SKILL.md")
    with pytest.raises(RuntimeError, match="not connected"):
        await group.read_skill("a", "skill://review/SKILL.md")


async def test_read_skill_preserves_extension_errors():
    class Broken(SkillCatalogProvider):
        async def _get_skill_entry(
            self, uri: str, context: ServerRequestContext[Any, Any]
        ) -> MCPSkill | None:
            raise MCPError(code=INTERNAL_ERROR, message="Catalog unavailable")

    server = FastMCP("Skills", providers=[Broken([REVIEW])])
    server.provider_error_strategy = "raise"
    async with skills_client(server) as client:
        with pytest.raises(MCPError) as exc:
            await client.read_skill("skill://review/SKILL.md")
        assert exc.value.error.code == INTERNAL_ERROR


@pytest.mark.parametrize("query", ["", "?ref=main"])
async def test_read_skill_preserves_manifest_resource_uris(query: str):
    main_uri = f"github://repo/review/SKILL.md{query}"
    original = REVIEW.to_mcp_skill()
    assert isinstance(original.resources, list)
    records = [
        record.model_copy(
            update={
                "uri": main_uri
                if record.uri.endswith("/SKILL.md")
                else record.uri.replace("skill://review/", "github://repo/review/")
                + "?ref=main"
            }
        )
        for record in original.resources
    ]
    entry = original.model_copy(update={"uri": main_uri, "resources": records})

    class Published(SkillsExtension):
        async def _get_entry(
            self, uri: str, context: ServerRequestContext[Any, Any]
        ) -> MCPSkill | None:
            return entry if uri == main_uri else None

    server = FastMCP("Custom URIs")
    server.add_extension(Published())
    for path, record in zip(REVIEW.files, records, strict=True):
        server.resource(record.uri)(_constant(REVIEW.files[path]))

    async with skills_client(server) as client:
        assert await client.read_skill(main_uri) == REVIEW
