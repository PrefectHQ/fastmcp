"""Example: Skills Client

This example shows how to discover and download skills from a skills server.

Run this client (it starts its own server internally):
    uv run python examples/skills/client.py
"""

import asyncio
import json
from pathlib import Path

from fastmcp import Client
from fastmcp.server.providers.skills import SkillsDirectoryProvider


async def main():
    # Create a skills provider pointing at our sample skills
    skills_dir = Path(__file__).parent / "sample_skills"
    provider = SkillsDirectoryProvider(roots=skills_dir)

    # Connect to a FastMCP server with this provider
    from fastmcp import FastMCP

    mcp = FastMCP("Skills Server")
    mcp.add_provider(provider)

    async with Client(mcp) as client:
        print("Connected to skills server\n")

        # List available resources
        print("=== Available Resources ===")
        resources = await client.list_resources()
        for r in resources:
            print(f"  {r.uri}")
            if r.description:
                print(f"    Description: {r.description}")
        print()

        # List resource templates
        print("=== Resource Templates ===")
        templates = await client.list_resource_templates()
        for t in templates:
            print(f"  {t.uriTemplate}")
        print()

        # Read a skill's main file
        print("=== Reading pdf-processing/SKILL.md ===")
        result = await client.read_resource("skill://pdf-processing/SKILL.md")
        print(result[0].text[:500] + "...\n")

        # Read the manifest to see all files
        print("=== Reading pdf-processing/_manifest ===")
        result = await client.read_resource("skill://pdf-processing/_manifest")
        manifest = json.loads(result[0].text)
        print(json.dumps(manifest, indent=2))
        print()

        # Read a supporting file via template
        print("=== Reading pdf-processing/reference.md ===")
        result = await client.read_resource("skill://pdf-processing/reference.md")
        print(result[0].text[:500] + "...\n")


if __name__ == "__main__":
    asyncio.run(main())
