"""`resources/directory/read` answered from skill manifests."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp import MCPError
from mcp_types import INVALID_PARAMS

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.server.transforms import Namespace
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills.extension import SkillsExtension, _directory_children


def write_skill(directory: Path, name: str) -> None:
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes(
        f"---\nname: {name}\ndescription: Use {name}.\n---\n".encode()
    )


@pytest.fixture
def provider(tmp_path: Path) -> SkillsDirectoryProvider:
    root = tmp_path / "acme" / "refunds"
    write_skill(root, "refunds")
    (root / "templates").mkdir()
    (root / "templates" / "invoice.md").write_bytes(b"# Invoice\n")
    (root / "templates" / "regional").mkdir()
    (root / "templates" / "regional" / "eu.md").write_bytes(b"# EU\n")
    write_skill(root / "partial", "partial")
    return SkillsDirectoryProvider(tmp_path, experimental_skills=True)


def server_for(
    provider: SkillsDirectoryProvider, *, directory_read: bool = True, **kwargs
) -> FastMCP:
    server = FastMCP("skills", providers=[provider], **kwargs)
    server.add_extension(
        SkillsExtension(providers=[provider], directory_read=directory_read)
    )
    return server


def skills_client(server: FastMCP) -> Client:
    return Client(server, mode="auto", extensions=[SkillsClientExtension()])


def listing(resources) -> list[tuple[str, str | None]]:
    return [(resource.uri, resource.mime_type) for resource in resources]


async def test_not_advertised_by_default(provider: SkillsDirectoryProvider) -> None:
    async with skills_client(server_for(provider, directory_read=False)) as client:
        assert client.server_capabilities is not None
        extensions = client.server_capabilities.extensions or {}
        assert extensions[SKILLS_EXTENSION_ID] == {}
        with pytest.raises(RuntimeError, match="directoryRead"):
            await client.read_resource_directory("skill://acme/refunds")


async def test_lists_direct_children(provider: SkillsDirectoryProvider) -> None:
    async with skills_client(server_for(provider)) as client:
        assert client.server_capabilities is not None
        extensions = client.server_capabilities.extensions or {}
        assert extensions[SKILLS_EXTENSION_ID] == {"directoryRead": True}

        root = await client.read_resource_directory("skill://acme/refunds")
        assert listing(root) == [
            ("skill://acme/refunds/SKILL.md", "text/markdown"),
            ("skill://acme/refunds/partial", "inode/directory"),
            ("skill://acme/refunds/templates", "inode/directory"),
        ]
        assert root[0].name == "SKILL.md"
        assert root[0].size == len(
            b"---\nname: refunds\ndescription: Use refunds.\n---\n"
        )

        templates = await client.read_resource_directory(
            "skill://acme/refunds/templates"
        )
        assert listing(templates) == [
            ("skill://acme/refunds/templates/invoice.md", "text/markdown"),
            ("skill://acme/refunds/templates/regional", "inode/directory"),
        ]
        assert templates[1].name == "regional"


async def test_lists_prefix_directories_above_skills(
    provider: SkillsDirectoryProvider,
) -> None:
    async with skills_client(server_for(provider)) as client:
        assert listing(await client.read_resource_directory("skill://acme")) == [
            ("skill://acme/refunds", "inode/directory")
        ]


@pytest.mark.parametrize(
    "uri",
    [
        "skill://acme/refunds/missing",
        "skill://acme/refunds/SKILL.md",
        "skill://acme/refunds/",
        "skill://other",
    ],
)
async def test_unknown_or_file_uri_is_invalid(
    provider: SkillsDirectoryProvider, uri: str
) -> None:
    async with skills_client(server_for(provider)) as client:
        with pytest.raises(MCPError) as error:
            await client.read_resource_directory(uri)
        assert error.value.error.code == INVALID_PARAMS


async def test_paginates(provider: SkillsDirectoryProvider) -> None:
    async with skills_client(server_for(provider, list_page_size=1)) as client:
        first = await client.read_resource_directory_mcp("skill://acme/refunds")
        assert len(first.resources) == 1
        assert first.next_cursor is not None
        assert len(await client.read_resource_directory("skill://acme/refunds")) == 3


async def test_follows_namespaces(provider: SkillsDirectoryProvider) -> None:
    provider.add_transform(Namespace("docs"))
    async with skills_client(server_for(provider)) as client:
        assert listing(await client.read_resource_directory("skill://docs/acme")) == [
            ("skill://docs/acme/refunds", "inode/directory")
        ]


def test_dynamic_skill_contributes_only_its_ancestors() -> None:
    skill = Skill.model_validate(
        {
            "uri": "skill://acme/generated/SKILL.md",
            "frontmatter": {"name": "generated", "description": "Generated"},
            "resources": "dynamic",
        }
    )

    children = _directory_children("skill://acme", [skill])
    assert children is not None
    assert listing(children) == [("skill://acme/generated", "inode/directory")]
    assert _directory_children("skill://acme/generated", [skill]) is None
