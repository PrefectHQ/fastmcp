"""FastMCP server implementation of the MCP Skills extension."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.mixins.mcp_operations import _apply_pagination
from fastmcp.server.providers.base import Provider
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills._providers import _AggregateSkills, _ServerSkills
from fastmcp.skills._source import _SkillSource
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsResult,
)

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP


class _SkillsExtension(ServerExtension, ABC):
    identifier = SKILLS_EXTENSION_ID

    @property
    @abstractmethod
    def _source(self) -> _SkillSource | None: ...

    def requires_live_resource(self, uri: str) -> bool:
        """Keep manifest-backed resources coherent with current file bytes."""
        source = self._source
        return source is not None and source._owns_skill_resource(uri)

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding(
                method="skills/list",
                params_type=ListSkillsParams,
                handler=self._handle_list,
                protocol_versions=frozenset(MODERN_PROTOCOL_VERSIONS),
            ),
            MethodBinding(
                method="skills/get",
                params_type=GetSkillParams,
                handler=self._handle_get,
                protocol_versions=frozenset(MODERN_PROTOCOL_VERSIONS),
            ),
        )

    def _require_source(self) -> _SkillSource:
        source = self._source
        if source is None:
            raise MCPError(code=METHOD_NOT_FOUND, message="Skills are not supported")
        return source

    async def _handle_list(
        self, context: ServerRequestContext[Any, Any], params: ListSkillsParams
    ) -> ListSkillsResult:
        catalog = await self._require_source()._list_skill_entries(context)
        page, cursor = _apply_pagination(
            catalog.result.skills, params.cursor, self.server._list_page_size
        )
        return catalog.result.model_copy(update={"skills": page, "next_cursor": cursor})

    async def _handle_get(
        self, context: ServerRequestContext[Any, Any], params: GetSkillParams
    ) -> GetSkillResult:
        lookup = await self._require_source()._get_skill_entry(params.uri, context)
        if (
            lookup.matches != 1
            or lookup.result is None
            or lookup.result.skill.uri != params.uri
        ):
            raise MCPError(
                code=INVALID_PARAMS, message=f"Unknown skill URI: {params.uri}"
            )
        return lookup.result


class SkillsExtension(_SkillsExtension):
    """Publish directly registered skill providers alongside proxied skills."""

    def __init__(self, *, providers: Sequence[Provider]) -> None:
        if not providers:
            raise ValueError("SkillsExtension requires at least one provider")
        if len({id(provider) for provider in providers}) != len(providers):
            raise ValueError("SkillsExtension providers must be unique")
        self._providers = tuple(providers)

    def _bind(self, server: FastMCP) -> None:
        self._validate_configuration(server)
        super()._bind(server)

    def _validate_configuration(self, server: FastMCP) -> tuple[_SkillSource, ...]:
        if server.transforms:
            raise ValueError(
                "SkillsExtension does not yet support server-level transforms"
            )
        sources = []
        for provider in self._providers:
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
                    f"{type(provider).__name__} is not a SkillsExtension source"
                )
            provider._validate_skill_source()
            sources.append(provider)
        return tuple(sources)

    @property
    def _source(self) -> _SkillSource | None:
        sources = list(self._validate_configuration(self.server))
        proxy = self.server._get_skills_source()
        if proxy is not None:
            sources.append(proxy)
        return _ServerSkills(
            _AggregateSkills(
                sources, error_strategy=self.server.provider_error_strategy
            ),
            self.server,
        )


class _ProxySkillsExtension(_SkillsExtension):
    """Optional forwarding handlers installed before a server starts serving."""

    @property
    def _source(self) -> _SkillSource | None:
        source = self.server._get_skills_source()
        return _ServerSkills(source, self.server) if source is not None else None

    async def _is_supported(self, context: ServerRequestContext[Any, Any]) -> bool:
        source = self._source
        return source is not None and await source._is_supported(context)
