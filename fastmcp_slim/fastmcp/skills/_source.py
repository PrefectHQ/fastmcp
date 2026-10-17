"""Private, dependency-light source contract for resource-backed Skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from mcp.server.context import ServerRequestContext

from fastmcp.skills.models import GetSkillResult, ListSkillsResult


@dataclass(frozen=True)
class _Catalog:
    result: ListSkillsResult
    identities: tuple[str, ...]


@dataclass(frozen=True)
class _Lookup:
    matches: int = 0
    result: GetSkillResult | None = None


class _SkillSource(ABC):
    """Keep identity before authorization alongside the typed protocol result."""

    async def _is_supported(self, context: ServerRequestContext[Any, Any]) -> bool:
        return True

    def _validate_skill_source(self) -> None:
        """Reject source configurations that cannot publish a Skills catalog."""
        return None

    def _owns_skill_resource(self, uri: str) -> bool:
        return False

    @abstractmethod
    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog: ...

    @abstractmethod
    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup: ...
