"""Serve sample Skills. Run with: uv run python -m examples.skills.server"""

from pathlib import Path

from fastmcp import FastMCP, Skill
from fastmcp.skills.provider import SkillCatalogProvider

root = Path(__file__).parent / "sample_skills"
skills = [Skill.from_directory(folder) for folder in sorted(root.iterdir())]
mcp = FastMCP("Skills Server", providers=[SkillCatalogProvider(skills)])


if __name__ == "__main__":
    mcp.run()
