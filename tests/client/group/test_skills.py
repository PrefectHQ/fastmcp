import pytest

from fastmcp import Client, ClientGroup, FastMCP
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills.provider import SkillCatalogProvider


def skill_server(name: str) -> FastMCP:
    skill = Skill(name=name, description=f"The {name} skill", instructions="# Hi\n")
    return FastMCP(name, providers=[SkillCatalogProvider([skill])])


def skills_client(server: FastMCP) -> Client:
    return Client(server, extensions=[SkillsClientExtension()])


async def test_list_skills_keys_entries_by_server():
    group = ClientGroup(
        {
            "a": skills_client(skill_server("review")),
            "b": skills_client(skill_server("review")),
        }
    )
    async with group:
        listed = await group.list_skills()
    assert {name: [e.uri for e in entries] for name, entries in listed.items()} == {
        "a": ["skill://review/SKILL.md"],
        "b": ["skill://review/SKILL.md"],
    }


async def test_list_skills_skips_clients_without_the_extension():
    group = ClientGroup(
        {
            "skills": skills_client(skill_server("review")),
            "opted_out": Client(skill_server("deploy")),
            "no_skills": skills_client(FastMCP("plain")),
        }
    )
    async with group:
        listed = await group.list_skills()
    assert list(listed) == ["skills"]


async def test_get_skill_reads_from_the_named_server():
    group = ClientGroup(
        {
            "a": skills_client(skill_server("review")),
            "b": skills_client(skill_server("deploy")),
        }
    )
    async with group:
        entry = await group.get_skill("b", "skill://deploy/SKILL.md")
        assert entry.frontmatter.description == "The deploy skill"
        with pytest.raises(KeyError, match="Unknown ClientGroup server"):
            await group.get_skill("c", "skill://deploy/SKILL.md")


async def test_get_skill_requires_a_connection():
    group = ClientGroup({"a": skills_client(skill_server("review"))})
    with pytest.raises(RuntimeError, match="not connected"):
        await group.get_skill("a", "skill://review/SKILL.md")
