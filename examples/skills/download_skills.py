"""Example: Downloading skills from an MCP server.

This example shows how to use the skills client utilities to discover
and download skills from any MCP server that exposes them via SkillsProvider.

Run this script:
    uv run python examples/skills/download_skills.py

This example creates an in-memory server with sample skills. In practice,
you would connect to a remote server URL instead.
"""

import asyncio
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.utilities.skills import list_skills, sync_skills

console = Console()


async def main():
    # For this example, we'll create an in-memory server with skills.
    # In practice, you'd connect to a remote server URL.
    skills_dir = Path(__file__).parent / "sample_skills"
    mcp = FastMCP("Skills Server")
    mcp.add_provider(SkillsDirectoryProvider(roots=skills_dir))

    async with Client(mcp) as client:
        # 1. Discover what skills are available on the server
        console.print()
        console.print(
            Panel.fit(
                "[bold]Discovering skills on MCP server...[/bold]",
                border_style="blue",
            )
        )

        skills = await list_skills(client)

        table = Table(title="Skills Available on Server", show_header=True)
        table.add_column("Skill", style="cyan")
        table.add_column("Description")
        for skill in sorted(skills, key=lambda s: s.name):
            table.add_row(skill.name, skill.description)
        console.print(table)

        # 2. Download all skills to a local directory
        console.print()
        console.print(
            Panel.fit(
                "[bold]Downloading all skills to local directory...[/bold]",
                border_style="green",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            paths = await sync_skills(client, tmp)

            tree = Tree(f"[bold]{tmp}[/bold]")
            for skill_path in sorted(paths, key=lambda p: p.name):
                skill_branch = tree.add(f"[cyan]{skill_path.name}/[/cyan]")
                for f in sorted(skill_path.rglob("*")):
                    if f.is_file():
                        rel = f.relative_to(skill_path)
                        skill_branch.add(str(rel))

            console.print(tree)
            console.print(
                f"\n[green]✓[/green] Downloaded {len(paths)} skills to local directory"
            )


if __name__ == "__main__":
    asyncio.run(main())
