"""Discover, read and export Skills: uv run python -m examples.skills.client"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.skills.server import mcp
from fastmcp import Client
from fastmcp.skills import SkillsClientExtension


async def main() -> None:
    async with Client(mcp, extensions=[SkillsClientExtension()]) as client:
        for entry in await client.list_skills():
            skill = await client.read_skill(entry.uri)
            print(f"{skill.name}: {skill.description}")
            print(f"Verified files: {', '.join(skill.files)}")
            with TemporaryDirectory() as directory:
                folder = skill.to_directory(Path(directory) / skill.name)
                print(f"Exported {folder.name} to a temporary directory")

    async with Client(mcp) as client:
        skill = await client.read_skill("skill://code-review/SKILL.md")
        print(f"Read without the Skills extension: {skill.name}")


if __name__ == "__main__":
    asyncio.run(main())
