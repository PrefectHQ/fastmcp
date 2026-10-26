"""Private, dependency-light source contract for resource-backed Skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcp.server.context import ServerRequestContext

from fastmcp.skills.models import Skill

if TYPE_CHECKING:
    from fastmcp.server.providers.proxy import ClientFactoryT
    from fastmcp.server.server import FastMCP


@dataclass(frozen=True)
class _Catalog:
    skills: list[Skill]
    identities: tuple[str, ...]
    owners: dict[str, ClientFactoryT | FastMCP] = field(default_factory=dict)


@dataclass(frozen=True)
class _Lookup:
    matches: int = 0
    skill: Skill | None = None
    owner: ClientFactoryT | FastMCP | None = None


class _SkillSource(ABC):
    """Keep identity before authorization alongside the visible skills."""

    def _validate_skill_source(self) -> None:
        """Reject source configurations that cannot publish a Skills catalog."""
        return None

    async def _owns_skill_resource(self, uri: str) -> bool:
        return False

    @abstractmethod
    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog: ...

    @abstractmethod
    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup: ...


def _manifest_uris(skill: Skill) -> list[str]:
    if isinstance(skill.resources, list):
        return [resource.uri for resource in skill.resources]
    return [skill.uri]
