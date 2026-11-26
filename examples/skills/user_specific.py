"""Run with uv run python -m examples.skills.user_specific."""

import asyncio

from fastmcp import FastMCP, Skill
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.skills import SkillsClientExtension
from fastmcp.skills.provider import SkillCatalogProvider
from fastmcp.utilities.tests import asgi_server

projects = {"alice": "Platform", "bob": "Developer Experience"}
routing_guides = {
    "Platform": "For a production outage, page the on-call engineer.",
    "Developer Experience": "For a broken quickstart, assign the docs maintainer.",
}


class UserSkills(SkillCatalogProvider):
    async def load_skills(self) -> list[Skill]:
        token = get_access_token()
        if token is None or token.subject is None:
            return []

        project = projects.get(token.subject)
        if project is None:
            return []

        skills = [
            Skill(
                name="issue-triage",
                description="Triage new issues for your project.",
                instructions=f"Triage open issues in {project}. Assign owners using routing.md.",
                files={"routing.md": routing_guides[project]},
            )
        ]
        if "releases:write" in token.scopes:
            skills.append(
                Skill(
                    name="release-notes",
                    description="Draft release notes for your project.",
                    instructions=f"Summarize recent changes in {project}.",
                )
            )
        return skills


auth = StaticTokenVerifier(
    tokens={
        "alice-token": {"client_id": "demo", "sub": "alice", "scopes": []},
        "bob-token": {
            "client_id": "demo",
            "sub": "bob",
            "scopes": ["releases:write"],
        },
    }
)
mcp = FastMCP("Project Skills", auth=auth, providers=[UserSkills()])


async def main() -> None:
    # Exercise the authenticated HTTP app in-process.
    async with asgi_server(mcp) as server:
        for user in projects:
            async with server.client(
                auth=f"{user}-token",
                extensions=[SkillsClientExtension()],
            ) as client:
                entries = await client.list_skills()
                names = ", ".join(sorted(entry.frontmatter.name for entry in entries))
                print(f"{user} can discover {names}")

                skill = await client.read_skill("skill://issue-triage/SKILL.md")
                print(skill.files["SKILL.md"].decode())
                print(skill.files["routing.md"].decode())


if __name__ == "__main__":
    asyncio.run(main())
