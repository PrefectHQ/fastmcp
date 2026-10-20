"""Private source contract consumed by `SkillsExtension`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from mcp.server.context import ServerRequestContext

from fastmcp.skills.models import Skill


@runtime_checkable
class _SkillSource(Protocol):
    """A provider capable of producing caller-scoped Skills entries."""

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> Sequence[Skill]: ...

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> Skill | None: ...

    def _validate_skill_source(self) -> None:
        """Reject sources that have not opted into protocol publication."""
        ...

    async def _skill_uris(self, uri: str | None = None) -> Sequence[str]:
        """Return identities before authorization/validation, preserving duplicates."""
        ...

    def _owns_skill_resource(self, uri: str) -> bool:
        """Whether manifest coherence requires this URI to be read live."""
        ...
