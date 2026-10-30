"""FastMCP server implementation of the MCP Skills extension."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.server.context import Context
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.mixins.mcp_operations import _apply_pagination
from fastmcp.server.providers.base import Provider
from fastmcp.skills._composition import _mounted_source
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills._source import _SkillSource
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsResult,
    MCPSkill,
)
from fastmcp.utilities.async_utils import gather
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)

_SKILLS_METHOD_VERSIONS = frozenset(MODERN_PROTOCOL_VERSIONS)
_T = TypeVar("_T")


class SkillsExtension(ServerExtension):
    """Expose caller-scoped skill sources through SEP-2640 methods.

    Skill providers register this extension automatically, so most servers
    never construct it. With no `providers`, it serves every skill source
    registered directly on the server, the backend skills of each
    `ProxyProvider(..., skills=True)`, and the skills of mounted servers,
    rewritten to the URIs the parent serves them at. Pass `providers` to limit
    discovery to an explicit list of directly registered skill providers.
    """

    identifier = SKILLS_EXTENSION_ID
    auto_register = True

    def __init__(self, *, providers: Sequence[Provider] | None = None) -> None:
        if providers is not None:
            if not providers:
                raise ValueError("SkillsExtension requires at least one provider")
            if len({id(provider) for provider in providers}) != len(providers):
                raise ValueError("SkillsExtension providers must be unique")
        self._providers = None if providers is None else tuple(providers)

    def clone(self) -> SkillsExtension:
        # A copy registered on a parent server discovers that server's sources.
        return SkillsExtension()

    def _bind(self, server: FastMCP) -> None:
        if self._providers is not None:
            self._validate_configuration(server, self._providers)
        super()._bind(server)

    def _validate_configuration(
        self, server: FastMCP, providers: Sequence[Provider]
    ) -> None:
        if server.transforms:
            raise ValueError(
                "SkillsExtension does not yet support server-level transforms"
            )

        for provider in providers:
            if not any(registered is provider for registered in server.providers):
                raise ValueError(
                    "SkillsExtension providers must be registered directly on the "
                    "same FastMCP server before the extension"
                )
            if provider.transforms:
                raise ValueError(
                    "SkillsExtension does not yet support transformed providers"
                )
            if not isinstance(provider, _SkillSource):
                raise TypeError(
                    f"{type(provider).__name__} is not a SkillsExtension source; "
                    "use SkillCatalogProvider to publish Skill objects"
                )

            provider._validate_skill_source()

    @property
    def _sources(self) -> tuple[_SkillSource, ...]:
        if self._providers is not None:
            self._validate_configuration(self.server, self._providers)
        return self._candidate_sources()

    def _candidate_sources(self) -> tuple[_SkillSource, ...]:
        server = self.server
        if self._providers is not None:
            return tuple(
                provider
                for provider in self._providers
                if isinstance(provider, _SkillSource)
            )
        if server.transforms:
            return ()
        # Deferred so that registering Skills doesn't load the client stack.
        from fastmcp.skills._proxy import _proxied_source

        sources: list[_SkillSource] = []
        for provider in server.providers:
            source = _mounted_source(provider) or _proxied_source(provider)
            if source is not None:
                sources.append(source)
            elif isinstance(provider, _SkillSource) and not provider.transforms:
                try:
                    provider._validate_skill_source()
                except ValueError:
                    continue
                sources.append(provider)
        return tuple(sources)

    def _requires_live_resource(self, uri: str) -> bool:
        """Keep manifest-backed resources coherent with current file bytes."""
        # Never raise here: response caching asks on every resource read.
        return any(
            source._owns_skill_resource(uri) for source in self._candidate_sources()
        )

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding(
                method="skills/list",
                params_type=ListSkillsParams,
                handler=self._handle_list,
                protocol_versions=_SKILLS_METHOD_VERSIONS,
            ),
            MethodBinding(
                method="skills/get",
                params_type=GetSkillParams,
                handler=self._handle_get,
                protocol_versions=_SKILLS_METHOD_VERSIONS,
            ),
        )

    async def _gather(self, operations: Iterable[Awaitable[_T]]) -> list[_T]:
        async with Context(fastmcp=self.server):
            results = await gather(operations, return_exceptions=True)
        successful = []
        for result in results:
            if isinstance(result, BaseException):
                if (
                    not isinstance(result, Exception)
                    or self.server._raises_on_provider_error()
                ):
                    raise result
                logger.warning("Error loading Skills source: %s", result)
            else:
                successful.append(result)
        return successful

    async def _identities(self, uri: str | None = None) -> list[str]:
        """Skill URIs before authorization, preserving duplicates across sources."""
        sources = self._sources
        groups = await self._gather(source._skill_uris(uri) for source in sources)
        return [identity for group in groups for identity in group]

    async def _list_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> list[MCPSkill]:
        sources = self._sources
        source_entries = await self._gather(
            source._list_skill_entries(context) for source in sources
        )
        counts = Counter(await self._identities())
        collisions = sorted(uri for uri, count in counts.items() if count > 1)
        if collisions:
            logger.warning(
                "Omitting duplicate Skills entries for URIs: %s",
                ", ".join(collisions),
            )
        return sorted(
            (
                entry
                for group in source_entries
                for entry in group
                if counts[entry.uri] == 1
            ),
            key=lambda entry: entry.uri,
        )

    async def _get_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> MCPSkill | None:
        if (await self._identities(uri)).count(uri) != 1:
            return None
        sources = self._sources
        results = await self._gather(
            source._get_skill_entry(uri, context) for source in sources
        )
        matches = [entry for entry in results if entry is not None]
        if len(matches) != 1 or matches[0].uri != uri:
            return None
        return matches[0]

    async def _handle_list(
        self,
        context: ServerRequestContext[Any, Any],
        params: ListSkillsParams,
    ) -> ListSkillsResult:
        entries = await self._list_entries(context)
        page, next_cursor = _apply_pagination(
            entries, params.cursor, self.server._list_page_size
        )
        return ListSkillsResult(skills=page, next_cursor=next_cursor)

    async def _handle_get(
        self,
        context: ServerRequestContext[Any, Any],
        params: GetSkillParams,
    ) -> GetSkillResult:
        entry = await self._get_entry(params.uri, context)
        if entry is None:
            raise MCPError(
                code=INVALID_PARAMS, message=f"Unknown skill URI: {params.uri}"
            )
        return GetSkillResult(skill=entry)
