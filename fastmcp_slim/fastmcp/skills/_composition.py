"""Skills delegation at aggregate, transform, and mounted-server boundaries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, TypeVar

from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp_types import METHOD_NOT_FOUND

from fastmcp.exceptions import AuthorizationError
from fastmcp.resources.base import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.context import Context
from fastmcp.server.transforms import Namespace, Visibility
from fastmcp.skills._source import _Catalog, _Lookup, _manifest_uris, _SkillSource
from fastmcp.skills.models import Skill
from fastmcp.utilities.async_utils import gather
from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from fastmcp.server.providers.aggregate import ProviderErrorStrategy
    from fastmcp.server.providers.base import Provider
    from fastmcp.server.providers.proxy import ClientFactoryT
    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)
_T = TypeVar("_T")


def _transformed(source: _SkillSource, provider: Provider) -> _SkillSource | None:
    if not provider.transforms:
        return source
    if any(not isinstance(t, Namespace | Visibility) for t in provider.transforms):
        return None
    return _TransformedSkills(source, provider)


class _AggregateSkills(_SkillSource):
    def __init__(
        self, sources: Sequence[_SkillSource], *, error_strategy: ProviderErrorStrategy
    ) -> None:
        self.sources = sources
        self.error_strategy = error_strategy

    async def _is_supported(self, context: ServerRequestContext[Any, Any]) -> bool:
        return any(
            self._successful(
                await gather(
                    (source._is_supported(context) for source in self.sources),
                    return_exceptions=True,
                )
            )
        )

    async def _owns_skill_resource(self, uri: str) -> bool:
        for source in self.sources:
            if await source._owns_skill_resource(uri):
                return True
        return False

    def _successful(self, results: Sequence[_T | BaseException]) -> list[_T]:
        successful: list[_T] = []
        unsupported = 0
        for result in results:
            if isinstance(result, MCPError) and result.error.code == METHOD_NOT_FOUND:
                unsupported += 1
            elif isinstance(result, BaseException):
                if self.error_strategy == "raise":
                    raise result
                logger.warning("Error during Skills discovery: %s", result)
            else:
                successful.append(result)
        if not successful and unsupported == len(results):
            raise MCPError(code=METHOD_NOT_FOUND, message="Skills are not supported")
        return successful

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog:
        catalogs = self._successful(
            await gather(
                (source._list_skill_entries(context) for source in self.sources),
                return_exceptions=True,
            )
        )
        identities = tuple(uri for catalog in catalogs for uri in catalog.identities)
        counts = Counter(identities)
        collisions = sorted(uri for uri, count in counts.items() if count > 1)
        if collisions:
            logger.warning(
                "Omitting duplicate Skills entries for URIs: %s", ", ".join(collisions)
            )
        skills = sorted(
            (
                skill
                for catalog in catalogs
                for skill in catalog.skills
                if counts[skill.uri] == 1
            ),
            key=lambda skill: skill.uri,
        )
        return _Catalog(
            skills,
            identities,
            {
                uri: owner
                for catalog in catalogs
                for uri, owner in catalog.owners.items()
                if counts[uri] == 1
            },
        )

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup:
        lookups = self._successful(
            await gather(
                (source._get_skill_entry(uri, context) for source in self.sources),
                return_exceptions=True,
            )
        )
        matches = sum(lookup.matches for lookup in lookups)
        skill = next(
            (lookup.skill for lookup in lookups if lookup.skill is not None), None
        )
        owner = next(
            (lookup.owner for lookup in lookups if lookup.skill is not None), None
        )
        return _Lookup(matches, skill if matches == 1 else None, owner)


class _TransformedSkills(_SkillSource):
    def __init__(self, source: _SkillSource, provider: Provider) -> None:
        self.source = source
        self.provider = provider

    def _forward(self, uri: str) -> str:
        for transform in self.provider.transforms:
            if isinstance(transform, Namespace):
                uri = transform._transform_uri(uri)
        return uri

    def _reverse(self, uri: str) -> str | None:
        for transform in reversed(self.provider.transforms):
            if isinstance(transform, Namespace):
                original = transform._reverse_uri(uri)
                if original is None:
                    return None
                uri = original
        return uri

    def _skill(self, skill: Skill) -> Skill:
        return skill.model_copy(
            update={
                "uri": self._forward(skill.uri),
                "resources": [
                    resource.model_copy(update={"uri": self._forward(resource.uri)})
                    for resource in skill.resources
                ]
                if isinstance(skill.resources, list)
                else skill.resources,
            }
        )

    async def _visible(self, skill: Skill) -> bool:
        if not any(isinstance(t, Visibility) for t in self.provider.transforms):
            return True
        for uri in _manifest_uris(skill):
            resource = await self.provider.get_resource(uri)
            if (
                resource is None
                and await self.provider.get_resource_template(uri) is None
            ):
                return False
        return True

    async def _is_supported(self, context: ServerRequestContext[Any, Any]) -> bool:
        return await self.source._is_supported(context)

    async def _owns_skill_resource(self, uri: str) -> bool:
        original = self._reverse(uri)
        return original is not None and await self.source._owns_skill_resource(original)

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog:
        catalog = await self.source._list_skill_entries(context)
        skills = [
            skill
            for skill in map(self._skill, catalog.skills)
            if await self._visible(skill)
        ]
        return _Catalog(
            skills,
            tuple(self._forward(uri) for uri in catalog.identities),
            {self._forward(uri): owner for uri, owner in catalog.owners.items()},
        )

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup:
        original = self._reverse(uri)
        if original is None:
            return _Lookup()
        lookup = await self.source._get_skill_entry(original, context)
        if lookup.skill is None:
            return lookup
        skill = self._skill(lookup.skill)
        return replace(lookup, skill=skill if await self._visible(skill) else None)


class _ServerSkills(_SkillSource):
    def __init__(self, source: _SkillSource, server: FastMCP) -> None:
        self.source = source
        self.server = server

    async def _is_supported(self, context: ServerRequestContext[Any, Any]) -> bool:
        async with Context(fastmcp=self.server):
            return await self.source._is_supported(context)

    async def _owns_skill_resource(self, uri: str) -> bool:
        return await self.source._owns_skill_resource(uri)

    async def _visible(
        self, skill: Skill, owner: ClientFactoryT | FastMCP | None
    ) -> bool:
        from fastmcp.server.providers.fastmcp_provider import (
            FastMCPProviderResource,
            FastMCPProviderResourceTemplate,
        )
        from fastmcp.server.providers.proxy import ProxyResource, ProxyTemplate

        try:
            for uri in _manifest_uris(skill):
                resource, exists = await self.server._lookup_resource(uri)
                component: Resource | ResourceTemplate | None = resource
                if component is None:
                    if exists:
                        return False
                    component = await self.server.get_resource_template(uri)
                if component is None:
                    return False
                if owner is not None:
                    if isinstance(component, ProxyResource | ProxyTemplate):
                        if component._client_factory is not owner:
                            return False
                    elif isinstance(
                        component,
                        FastMCPProviderResource | FastMCPProviderResourceTemplate,
                    ):
                        if component._server is not owner:
                            return False
                    else:
                        return False
                await self.server._check_resource_access(uri)
        except AuthorizationError:
            return False
        return True

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog:
        async with Context(fastmcp=self.server):
            catalog = await self.source._list_skill_entries(context)
            skills = [
                skill
                for skill in catalog.skills
                if await self._visible(skill, catalog.owners.get(skill.uri))
            ]
        return replace(
            catalog, skills=skills, owners={skill.uri: self.server for skill in skills}
        )

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup:
        async with Context(fastmcp=self.server):
            lookup = await self.source._get_skill_entry(uri, context)
            if lookup.skill is None:
                return lookup
            visible = await self._visible(lookup.skill, lookup.owner)
        return replace(
            lookup, skill=lookup.skill if visible else None, owner=self.server
        )
