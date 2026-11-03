"""Supporting-file content stays consistent across resource publication modes."""

import base64
from pathlib import Path
from typing import Literal

import pytest
from mcp_types import BlobResourceContents, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillProvider


@pytest.mark.parametrize("supporting_files", ["template", "resources"])
@pytest.mark.parametrize(
    ("filename", "content", "is_text"),
    [
        ("notes.md", "Résumé\r\n".encode(), True),
        ("data.bin", b"\x00\xff\r\n", False),
        ("unknown-extension", b"\xff\x00", False),
    ],
)
async def test_supporting_file_preserves_content(
    tmp_path: Path,
    supporting_files: Literal["template", "resources"],
    filename: str,
    content: bytes,
    is_text: bool,
) -> None:
    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"# Review\n")
    (root / filename).write_bytes(content)
    provider = SkillProvider(root, supporting_files=supporting_files)
    server = FastMCP("Skills", providers=[provider])

    async with Client(server) as client:
        (result,) = await client.read_resource(f"skill://review/{filename}")

    if is_text:
        assert isinstance(result, TextResourceContents)
        assert result.text.encode("utf-8") == content
    else:
        assert isinstance(result, BlobResourceContents)
        assert base64.b64decode(result.blob) == content


@pytest.mark.parametrize("supporting_files", ["template", "resources"])
async def test_supporting_text_still_rejects_invalid_utf8(
    tmp_path: Path, supporting_files: Literal["template", "resources"]
) -> None:
    root = tmp_path / "review"
    root.mkdir()
    (root / "SKILL.md").write_bytes(b"# Review\n")
    (root / "notes.md").write_bytes(b"\xff")
    provider = SkillProvider(root, supporting_files=supporting_files)

    with pytest.raises(UnicodeDecodeError):
        if supporting_files == "template":
            (template,) = await provider.list_resource_templates()
            await template.read({"path": "notes.md"})
        else:
            resource = await provider.get_resource("skill://review/notes.md")
            assert resource is not None
            await resource.read()
