import json
from typing import Any
from unittest.mock import patch

import pytest

from fastmcp import Client, FastMCP
from fastmcp.cli import client as client_module
from fastmcp.cli.client import list_command
from fastmcp.skills import Skill, SkillsClientExtension
from fastmcp.skills.provider import SkillCatalogProvider

REVIEW = Skill(
    name="review",
    description="Review changes",
    instructions="# Review\n",
    files={"notes.md": "notes\n"},
)


def patch_client(server: FastMCP):
    def fake_build_client(resolved: Any, *, skills: bool = False, **_: Any) -> Client:
        extensions = [SkillsClientExtension()] if skills else None
        return Client(server, extensions=extensions)

    return (
        patch.object(client_module, "resolve_server_spec", return_value="fake"),
        patch.object(client_module, "_build_client", side_effect=fake_build_client),
    )


async def test_list_skills_text(capsys: pytest.CaptureFixture[str]):
    resolve, build = patch_client(
        FastMCP("s", providers=[SkillCatalogProvider([REVIEW])])
    )
    with resolve, build:
        await list_command("fake", skills=True)
    out = capsys.readouterr().out
    assert "Skills (1)" in out
    assert "skill://review/SKILL.md" in out
    assert "Review changes" in out
    assert "skill://review/notes.md (6 bytes)" in out


async def test_list_skills_json(capsys: pytest.CaptureFixture[str]):
    resolve, build = patch_client(
        FastMCP("s", providers=[SkillCatalogProvider([REVIEW])])
    )
    with resolve, build:
        await list_command("fake", skills=True, json_output=True)
    (entry,) = json.loads(capsys.readouterr().out)["skills"]
    assert entry == REVIEW.to_mcp_skill().model_dump(
        mode="json", by_alias=True, exclude_none=True
    )


async def test_list_skills_without_server_support(capsys: pytest.CaptureFixture[str]):
    resolve, build = patch_client(FastMCP("plain"))
    with resolve, build:
        await list_command("fake", skills=True)
    out = capsys.readouterr().out
    assert "Skills (0)" in out
    assert "does not advertise the Skills extension" in out


async def test_build_client_opts_into_skills():
    client = client_module._build_client(
        "http://localhost:8000/mcp", auth="none", skills=True
    )
    assert "io.modelcontextprotocol/skills" in (
        client._session_kwargs.get("extensions") or {}
    )
