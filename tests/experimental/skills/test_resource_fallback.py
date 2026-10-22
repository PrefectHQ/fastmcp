import base64
import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import BlobResourceContents, TextResourceContents

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills.extension import SkillsExtension


@pytest.mark.parametrize("mode", ["template", "resources"])
@pytest.mark.parametrize(
    "main",
    [
        b"\xff\xfeNot UTF-8",
        b"---\nname: invalid\ndescription: [one, two]\n---\nBody\n",
        b"---\nname: invalid\ndescription: {nested: value}\n---\nBody\n",
    ],
    ids=["invalid-utf8", "list-description", "mapping-description"],
)
async def test_invalid_skill_remains_an_ordinary_resource(
    tmp_path: Path, mode: Literal["template", "resources"], main: bytes
):
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    files = {"SKILL.md": main, "notes.txt": b"supporting\r\nbytes\n"}
    for name, data in files.items():
        (invalid / name).write_bytes(data)
    valid = tmp_path / "valid"
    valid.mkdir()
    (valid / "SKILL.md").write_bytes(
        b"---\nname: valid\ndescription: Valid neighbor\n---\n"
    )
    provider = SkillsDirectoryProvider(
        tmp_path, experimental_skills=True, supporting_files=mode
    )
    server = FastMCP("Skills", providers=[provider])
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        resources = {str(r.uri): r for r in await client.list_resources()}
        assert "skill://invalid/SKILL.md" in resources
        assert "skill://invalid/_manifest" in resources
        assert "skill://valid/SKILL.md" in resources
        assert isinstance(resources["skill://invalid/SKILL.md"].description, str)
        if mode == "resources":
            assert "skill://invalid/notes.txt" in resources
        for name, expected in files.items():
            content = (await client.read_resource(f"skill://invalid/{name}"))[0]
            if isinstance(content, TextResourceContents):
                actual = content.text.encode()
            else:
                assert isinstance(content, BlobResourceContents)
                actual = base64.b64decode(content.blob)
            assert actual == expected
        manifest = (await client.read_resource("skill://invalid/_manifest"))[0]
        assert isinstance(manifest, TextResourceContents)
        manifest_data = json.loads(manifest.text)
        manifest_data["files"].sort(key=lambda entry: entry["path"])
        assert manifest_data == {
            "skill": "invalid",
            "files": [
                {
                    "path": name,
                    "size": len(data),
                    "hash": f"sha256:{hashlib.sha256(data).hexdigest()}",
                }
                for name, data in sorted(files.items())
            ],
        }
        assert [s.uri for s in await client.list_skills()] == ["skill://valid/SKILL.md"]
        with pytest.raises(MCPError, match="Unknown skill URI"):
            await client.get_skill("skill://invalid/SKILL.md")
