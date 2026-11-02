"""Discover, read and export Skills: uv run python -m examples.skills.client"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.skills.server import mcp
from fastmcp import Client
from fastmcp.skills import SkillsClientExtension
from fastmcp.utilities.skills import list_skill_summaries


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
        summaries = await list_skill_summaries(client)
        skill = await client.read_skill(summaries[0].uri)
        print(f"Resource-only discovery and download: {skill.name}")


if __name__ == "__main__":
    asyncio.run(main())
