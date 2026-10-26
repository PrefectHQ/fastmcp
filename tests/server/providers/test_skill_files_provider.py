"""Skills published from loaded bytes instead of a directory."""

import base64
import hashlib
from collections.abc import Mapping

from mcp_types import BlobResourceContents, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillFilesProvider
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills.extension import SkillsExtension

DEPLOY = b"---\nname: deploy\ndescription: Deploy the service.\n---\n\n# Deploy\n"
LOGO = b"\x89PNG\r\n\x1a\n\x00"


class StoredSkills(SkillFilesProvider):
    def __init__(self, files: Mapping[str, Mapping[str, bytes]]) -> None:
        super().__init__()
        self.files = files
        self.loads = 0

    async def skill_files(self) -> Mapping[str, Mapping[str, bytes]]:
        self.loads += 1
        return self.files


def skills_server(provider: StoredSkills) -> FastMCP:
    server = FastMCP("skills")
    server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=[provider]))
    return server


def skills_client(server: FastMCP) -> Client:
    return Client(server, mode="auto", extensions=[SkillsClientExtension()])


async def test_entry_is_built_from_the_loaded_bytes() -> None:
    provider = StoredSkills({"deploy": {"SKILL.md": DEPLOY, "logo.png": LOGO}})
    async with skills_client(skills_server(provider)) as client:
        [skill] = await client.list_skills()

    assert skill.uri == "skill://deploy/SKILL.md"
    assert skill.frontmatter.description == "Deploy the service."
    assert isinstance(skill.resources, list)
    assert [(r.uri, r.size, r.digest) for r in skill.resources] == [
        (uri, len(data), f"sha256:{hashlib.sha256(data).hexdigest()}")
        for uri, data in [
            ("skill://deploy/SKILL.md", DEPLOY),
            ("skill://deploy/logo.png", LOGO),
        ]
    ]


async def test_files_are_served_unchanged() -> None:
    provider = StoredSkills({"deploy": {"SKILL.md": DEPLOY, "logo.png": LOGO}})
    async with skills_client(skills_server(provider)) as client:
        [main] = await client.read_resource("skill://deploy/SKILL.md")
        [logo] = await client.read_resource("skill://deploy/logo.png")

    assert isinstance(main, TextResourceContents)
    assert main.text.encode() == DEPLOY
    assert isinstance(logo, BlobResourceContents)
    assert base64.b64decode(logo.blob) == LOGO


async def test_get_skill_returns_the_listed_entry() -> None:
    provider = StoredSkills({"deploy": {"SKILL.md": DEPLOY}})
    async with skills_client(skills_server(provider)) as client:
        [listed] = await client.list_skills()
        fetched = await client.get_skill("skill://deploy/SKILL.md")

    assert fetched == listed


async def test_listing_loads_the_files_once() -> None:
    provider = StoredSkills(
        {
            name: {
                "SKILL.md": f"---\nname: {name}\ndescription: Use {name}.\n---\n".encode(),
                "logo.png": LOGO,
                "guide.md": b"guide",
            }
            for name in ("build", "deploy", "review", "test", "write")
        }
    )
    async with skills_client(skills_server(provider)) as client:
        loads_before_listing = provider.loads
        assert len(await client.list_skills()) == 5
        assert provider.loads == loads_before_listing + 1


async def test_nested_skill_path_is_part_of_the_uri() -> None:
    provider = StoredSkills({"team/deploy": {"SKILL.md": DEPLOY}})
    async with skills_client(skills_server(provider)) as client:
        assert [skill.uri for skill in await client.list_skills()] == [
            "skill://team/deploy/SKILL.md"
        ]


async def test_invalid_skill_is_omitted_and_others_still_served() -> None:
    provider = StoredSkills(
        {
            "broken": {"SKILL.md": b"no frontmatter"},
            "renamed": {"SKILL.md": DEPLOY},
            "deploy": {"SKILL.md": DEPLOY},
        }
    )
    async with skills_client(skills_server(provider)) as client:
        assert [s.uri for s in await client.list_skills()] == [
            "skill://deploy/SKILL.md"
        ]
        uris = {str(r.uri) for r in await client.list_resources()}

    assert "skill://broken/SKILL.md" not in uris
    assert "skill://deploy/SKILL.md" in uris


async def test_skill_is_hidden_when_another_provider_serves_its_file() -> None:
    server = FastMCP("skills")

    @server.resource("skill://deploy/SKILL.md")
    def shadow() -> str:
        return "different bytes"

    provider = StoredSkills({"deploy": {"SKILL.md": DEPLOY}})
    server.add_provider(provider)
    server.add_extension(SkillsExtension(providers=[provider]))
    async with skills_client(server) as client:
        assert await client.list_skills() == []
