from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills.extension import SkillsExtension


@pytest.mark.parametrize("mode", ["template", "resources"])
async def test_discovery_work_scales_with_requested_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["template", "resources"],
):
    for index in range(20):
        directory = tmp_path / f"skill-{index}"
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: skill-{index}\ndescription: Test skill\n---\n"
        )
        for file_index in range(4):
            (directory / f"file-{file_index}.txt").write_text("supporting file")

    scans: list[Path] = []
    reads: list[Path] = []
    original_rglob = Path.rglob
    original_read_bytes = Path.read_bytes

    def counted_rglob(path, *args, **kwargs):
        scans.append(path)
        return original_rglob(path, *args, **kwargs)

    def counted_read_bytes(path):
        reads.append(path)
        return original_read_bytes(path)

    provider = SkillsDirectoryProvider(
        tmp_path, experimental_skills=True, supporting_files=mode
    )
    server = FastMCP("Skills", providers=[provider])
    server.add_extension(SkillsExtension(providers=[provider]))
    async with Client(
        server, mode="auto", extensions=[SkillsClientExtension()]
    ) as client:
        with monkeypatch.context() as instrumentation:
            instrumentation.setattr(Path, "rglob", counted_rglob)
            instrumentation.setattr(Path, "read_bytes", counted_read_bytes)
            entries = await client.list_skills()
            assert len(entries) == 20
            assert len(scans) <= 2 + 2 * len(entries)
            assert len(reads) == 100

            scans.clear()
            reads.clear()
            entry = await client.get_skill("skill://skill-0/SKILL.md")
            assert entry.uri == "skill://skill-0/SKILL.md"
            assert len(scans) <= 2
            assert len(reads) == 5
            assert all(path.parent == tmp_path / "skill-0" for path in reads)

            scans.clear()
            contents = await client.read_resource("skill://skill-0/file-0.txt")
            assert contents[0].text == "supporting file"
            assert scans == []

        added = tmp_path / "added"
        added.mkdir()
        (added / "SKILL.md").write_text(
            "---\nname: added\ndescription: Added live\n---\n"
        )
        assert (
            await client.get_skill("skill://added/SKILL.md")
        ).frontmatter.name == "added"
