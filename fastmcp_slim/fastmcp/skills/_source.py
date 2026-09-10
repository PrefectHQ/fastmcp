"""Private source contract consumed by `SkillsExtension`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from mcp.server.context import ServerRequestContext

from fastmcp.skills.models import Skill


@runtime_checkable
class SkillSource(Protocol):
    """A provider capable of producing caller-scoped Skills entries."""

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> Sequence[Skill]: ...

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> Skill | None: ...
