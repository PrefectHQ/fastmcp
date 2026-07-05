"""Tests for skills client utilities."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import cast

import pytest
from mcp_types import BlobResourceContents, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.utilities.skills import (
    SkillFile,
    SkillManifest,
    SkillSummary,
    download_skill,
    get_skill_manifest,
    list_skills,
    sync_skills,
)


class FakeResourceReader:
    def __init__(
        self,
        responses: dict[str, list[TextResourceContents | BlobResourceContents]],
    ) -> None:
        self.responses = responses
        self.requested_uris: list[str] = []

    async def read_resource(self, uri: str):
        self.requested_uris.append(uri)
        return self.responses.get(uri, [])


def text_resource(uri: str, text: str) -> TextResourceContents:
    return TextResourceContents(uri=uri, text=text)


def blob_resource(uri: str, data: bytes) -> BlobResourceContents:
    return BlobResourceContents(
        uri=uri,
        blob=base64.b64encode(data).decode(),
    )


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a temporary skills directory with sample skills."""
    skills = tmp_path / "skills"
    skills.mkdir()

    # Create pdf-processing skill
    pdf_skill = skills / "pdf-processing"
    pdf_skill.mkdir()
    (pdf_skill / "SKILL.md").write_text(
        """---
description: Process PDF documents
---

# PDF Processing

Instructions for PDF handling.
"""
    )
    (pdf_skill / "reference.md").write_text("# Reference\n\nSome reference docs.")

    # Create code-review skill
    code_skill = skills / "code-review"
    code_skill.mkdir()
    (code_skill / "SKILL.md").write_text(
        """---
description: Review code for quality
---

# Code Review

Instructions for reviewing code.
"""
    )

    # Create skill with nested files
    nested_skill = skills / "nested-skill"
    nested_skill.mkdir()
    (nested_skill / "SKILL.md").write_text("# Nested\n\nHas nested files.")
    scripts = nested_skill / "scripts"
    scripts.mkdir()
    (scripts / "helper.py").write_text("# Helper script\nprint('hello')")

    return skills


@pytest.fixture
def skills_server(skills_dir: Path) -> FastMCP:
    """Create a FastMCP server with skills provider."""
    mcp = FastMCP("Skills Server")
    mcp.add_provider(SkillsDirectoryProvider(roots=skills_dir))
    return mcp


class TestListSkills:
    async def test_lists_available_skills(self, skills_server: FastMCP):
        async with Client(skills_server) as client:
            skills = await list_skills(client)

        assert len(skills) == 3
        names = {s.name for s in skills}
        assert names == {"pdf-processing", "code-review", "nested-skill"}

    async def test_returns_skill_summary_objects(self, skills_server: FastMCP):
        async with Client(skills_server) as client:
            skills = await list_skills(client)

        for skill in skills:
            assert isinstance(skill, SkillSummary)
            assert skill.name
            assert skill.uri.startswith("skill://")
            assert skill.uri.endswith("/SKILL.md")

    async def test_includes_descriptions(self, skills_server: FastMCP):
        async with Client(skills_server) as client:
            skills = await list_skills(client)

        by_name = {s.name: s for s in skills}
        assert by_name["pdf-processing"].description == "Process PDF documents"
        assert by_name["code-review"].description == "Review code for quality"

    async def test_empty_server_returns_empty_list(self):
        mcp = FastMCP("Empty")
        async with Client(mcp) as client:
            skills = await list_skills(client)

        assert skills == []

    async def test_filters_non_skill_resources(self):
        mcp = FastMCP("Mixed Resources")

        @mcp.resource("skill://team/pdf/SKILL.md")
        def skill_document():
            return "# Team PDF"

        @mcp.resource("skill://team/pdf/reference.md")
        def skill_reference():
            return "# Reference"

        @mcp.resource("file:///tmp/SKILL.md")
        def non_skill_document():
            return "# File"

        @mcp.resource("skill://broken/SKILL.txt")
        def wrong_suffix():
            return "# Wrong suffix"

        async with Client(mcp) as client:
            skills = await list_skills(client)

        assert skills == [
            SkillSummary(
                name="team/pdf",
                description="",
                uri="skill://team/pdf/SKILL.md",
            )
        ]


class TestGetSkillManifest:
    async def test_returns_manifest_with_files(self, skills_server: FastMCP):
        async with Client(skills_server) as client:
            manifest = await get_skill_manifest(client, "pdf-processing")

        assert isinstance(manifest, SkillManifest)
        assert manifest.name == "pdf-processing"
        assert len(manifest.files) == 2

        paths = {f.path for f in manifest.files}
        assert paths == {"SKILL.md", "reference.md"}

    async def test_files_have_size_and_hash(self, skills_server: FastMCP):
        async with Client(skills_server) as client:
            manifest = await get_skill_manifest(client, "pdf-processing")

        for file in manifest.files:
            assert isinstance(file, SkillFile)
            assert file.size > 0
            assert file.hash.startswith("sha256:")

    async def test_nested_files_use_posix_paths(self, skills_server: FastMCP):
        async with Client(skills_server) as client:
            manifest = await get_skill_manifest(client, "nested-skill")

        paths = {f.path for f in manifest.files}
        assert "scripts/helper.py" in paths

    async def test_nonexistent_skill_raises(self, skills_server: FastMCP):
        async with Client(skills_server) as client:
            with pytest.raises(Exception):
                await get_skill_manifest(client, "nonexistent")

    @pytest.mark.parametrize(
        ("response", "match"),
        [
            ([], "Could not read manifest"),
            (
                [text_resource("skill://broken/_manifest", "not json")],
                "Invalid manifest JSON",
            ),
            (
                [blob_resource("skill://broken/_manifest", b"{}")],
                "Unexpected manifest format",
            ),
            (
                [text_resource("skill://broken/_manifest", '{"skill": "broken"}')],
                "Invalid manifest format",
            ),
        ],
    )
    async def test_invalid_manifest_responses_raise_value_error(
        self,
        response: list[TextResourceContents | BlobResourceContents],
        match: str,
    ):
        client = FakeResourceReader({"skill://broken/_manifest": response})

        with pytest.raises(ValueError, match=match):
            await get_skill_manifest(cast(Client, client), "broken")


class TestDownloadSkill:
    async def test_downloads_skill_to_directory(
        self, skills_server: FastMCP, tmp_path: Path
    ):
        target = tmp_path / "downloaded"
        target.mkdir()

        async with Client(skills_server) as client:
            result = await download_skill(client, "pdf-processing", target)

        assert result == target / "pdf-processing"
        assert result.exists()
        assert (result / "SKILL.md").exists()
        assert (result / "reference.md").exists()

    async def test_creates_nested_directories(
        self, skills_server: FastMCP, tmp_path: Path
    ):
        target = tmp_path / "downloaded"
        target.mkdir()

        async with Client(skills_server) as client:
            result = await download_skill(client, "nested-skill", target)

        assert (result / "scripts" / "helper.py").exists()
        content = (result / "scripts" / "helper.py").read_text()
        assert "print('hello')" in content

    async def test_preserves_file_content(
        self, skills_server: FastMCP, tmp_path: Path, skills_dir: Path
    ):
        target = tmp_path / "downloaded"
        target.mkdir()

        async with Client(skills_server) as client:
            result = await download_skill(client, "pdf-processing", target)

        original = (skills_dir / "pdf-processing" / "SKILL.md").read_text()
        downloaded = (result / "SKILL.md").read_text()
        assert downloaded == original

    async def test_raises_if_exists_without_overwrite(
        self, skills_server: FastMCP, tmp_path: Path
    ):
        target = tmp_path / "downloaded"
        target.mkdir()
        (target / "pdf-processing").mkdir()

        async with Client(skills_server) as client:
            with pytest.raises(FileExistsError):
                await download_skill(client, "pdf-processing", target)

    async def test_overwrites_with_flag(self, skills_server: FastMCP, tmp_path: Path):
        target = tmp_path / "downloaded"
        target.mkdir()
        existing = target / "pdf-processing"
        existing.mkdir()
        (existing / "old-file.txt").write_text("old content")

        async with Client(skills_server) as client:
            result = await download_skill(
                client, "pdf-processing", target, overwrite=True
            )

        assert (result / "SKILL.md").exists()

    async def test_expands_user_path(self, skills_server: FastMCP, tmp_path: Path):
        # This tests that ~ expansion works (though we can't actually test ~)
        async with Client(skills_server) as client:
            result = await download_skill(client, "code-review", tmp_path)

        assert result.exists()

    async def test_skips_manifest_paths_that_escape_target(self, tmp_path: Path):
        manifest = {
            "skill": "malicious",
            "files": [
                {"path": "SKILL.md", "size": 6, "hash": "sha256:safe"},
                {"path": "../escape.txt", "size": 6, "hash": "sha256:escape"},
                {"path": "/absolute.txt", "size": 8, "hash": "sha256:absolute"},
            ],
        }

        client = FakeResourceReader(
            {
                "skill://malicious/_manifest": [
                    text_resource("skill://malicious/_manifest", json.dumps(manifest))
                ],
                "skill://malicious/SKILL.md": [
                    text_resource("skill://malicious/SKILL.md", "# Safe")
                ],
            }
        )

        result = await download_skill(cast(Client, client), "malicious", tmp_path)

        assert (result / "SKILL.md").read_text() == "# Safe"
        assert not (tmp_path / "escape.txt").exists()
        assert client.requested_uris == [
            "skill://malicious/_manifest",
            "skill://malicious/SKILL.md",
        ]

    async def test_downloads_blob_resources(self, tmp_path: Path):
        data = b"\x00\x01binary data"
        manifest = {
            "skill": "binary",
            "files": [{"path": "data.bin", "size": len(data), "hash": "sha256:data"}],
        }

        client = FakeResourceReader(
            {
                "skill://binary/_manifest": [
                    text_resource("skill://binary/_manifest", json.dumps(manifest))
                ],
                "skill://binary/data.bin": [
                    blob_resource("skill://binary/data.bin", data)
                ],
            }
        )

        result = await download_skill(cast(Client, client), "binary", tmp_path)

        assert (result / "data.bin").read_bytes() == data


class TestSyncSkills:
    async def test_downloads_all_skills(self, skills_server: FastMCP, tmp_path: Path):
        target = tmp_path / "synced"
        target.mkdir()

        async with Client(skills_server) as client:
            results = await sync_skills(client, target)

        assert len(results) == 3
        assert (target / "pdf-processing").exists()
        assert (target / "code-review").exists()
        assert (target / "nested-skill").exists()

    async def test_skips_existing_without_overwrite(
        self, skills_server: FastMCP, tmp_path: Path
    ):
        target = tmp_path / "synced"
        target.mkdir()
        (target / "pdf-processing").mkdir()

        async with Client(skills_server) as client:
            results = await sync_skills(client, target)

        # Should skip pdf-processing, download the other two
        assert len(results) == 2
        names = {r.name for r in results}
        assert "pdf-processing" not in names

    async def test_overwrites_with_flag(self, skills_server: FastMCP, tmp_path: Path):
        target = tmp_path / "synced"
        target.mkdir()
        (target / "pdf-processing").mkdir()

        async with Client(skills_server) as client:
            results = await sync_skills(client, target, overwrite=True)

        assert len(results) == 3

    async def test_returns_paths_to_downloaded_skills(
        self, skills_server: FastMCP, tmp_path: Path
    ):
        target = tmp_path / "synced"
        target.mkdir()

        async with Client(skills_server) as client:
            results = await sync_skills(client, target)

        for path in results:
            assert isinstance(path, Path)
            assert path.exists()
            assert (path / "SKILL.md").exists()


class TestPathTraversal:
    @pytest.mark.parametrize(
        "malicious_name",
        [
            "../escape",
            "../../root",
            "../../../etc/passwd",
            "foo/../../escape",
        ],
    )
    async def test_malicious_skill_name_raises(
        self, skills_server: FastMCP, tmp_path: Path, malicious_name: str
    ):
        target = tmp_path / "downloaded"
        target.mkdir()

        async with Client(skills_server) as client:
            with pytest.raises(ValueError, match="would escape the target directory"):
                await download_skill(client, malicious_name, target)
