from __future__ import annotations

import pytest
from mcp_types import TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.skills import Skill, SkillFile, SkillManifest, SkillsClientExtension
from fastmcp.skills.provider import SkillManifestProvider

REVIEW = Skill(
    name="review",
    description="Review changes",
    instructions="# Review\n\nRead notes.md first.\n",
    files={"notes.md": "line one\n"},
)


class StoredSkills(SkillManifestProvider):
    def __init__(self, *skills: Skill) -> None:
        super().__init__()
        self.manifests = {skill.name: skill.manifest for skill in skills}
        self.blobs = {
            file.digest: skill.files[file.path]
            for skill in skills
            for file in skill.manifest.files
        }
        self.reads: list[str] = []

    async def load_manifests(self) -> dict[str, SkillManifest]:
        return self.manifests

    async def read_file(self, path: str, file: SkillFile) -> bytes:
        self.reads.append(f"{path}/{file.path}")
        return self.blobs[file.digest]


def skills_client(provider: SkillManifestProvider) -> Client:
    server = FastMCP("Skills", providers=[provider])
    return Client(server, extensions=[SkillsClientExtension()])


async def test_lists_skills_without_reading_their_files():
    store = StoredSkills(REVIEW)

    async with skills_client(store) as client:
        entries = await client.list_skills()
        await client.list_resources()

    assert entries == [REVIEW.to_mcp_skill()]
    assert store.reads == []


async def test_reads_only_the_file_a_client_reads():
    store = StoredSkills(REVIEW)

    async with skills_client(store) as client:
        (content,) = await client.read_resource("skill://review/notes.md")

    assert isinstance(content, TextResourceContents)
    assert content.text == "line one\n"
    assert store.reads == ["review/notes.md"]


async def test_read_skill_returns_the_stored_skill():
    async with skills_client(StoredSkills(REVIEW)) as client:
        skill = await client.read_skill("skill://review/SKILL.md")

    assert skill == REVIEW


def test_manifest_requires_skill_md():
    files = tuple(file for file in REVIEW.manifest.files if file.path != "SKILL.md")

    with pytest.raises(ValueError, match="must contain SKILL.md"):
        SkillManifest(frontmatter=REVIEW.manifest.frontmatter, files=files)


def test_manifest_rejects_a_repeated_path():
    files = REVIEW.manifest.files + REVIEW.manifest.files[:1]

    with pytest.raises(ValueError, match="may appear only once"):
        SkillManifest(frontmatter=REVIEW.manifest.frontmatter, files=files)
