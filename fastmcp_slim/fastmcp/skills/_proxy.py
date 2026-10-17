"""Resource-backed Skills metadata forwarded through a proxy's backend client."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS, METHOD_NOT_FOUND
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.client.client import Client
from fastmcp.server.dependencies import get_context
from fastmcp.server.middleware.caching import _get_auth_partition_key
from fastmcp.server.providers.proxy import (
    _DEFAULT_CACHE_TTL,
    _PROXY_TRANSPORT_ERRORS,
    ProxyMetadataMiddleware,
    _CacheEntry,
    _forwardable_request_meta,
    _proxy_upstream_error,
)
from fastmcp.skills import _client
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills._source import _Catalog, _Lookup, _SkillSource
from fastmcp.skills.models import ListSkillsResult, Skill

if TYPE_CHECKING:
    from fastmcp.server.providers.proxy import ProxyProvider


class _ProxySkillSource(_SkillSource):
    def __init__(self, provider: ProxyProvider) -> None:
        self.provider = provider
        self._manifests: dict[tuple[str, str], _CacheEntry[str]] = {}

    async def _is_supported(self, context: ServerRequestContext[Any, Any]) -> bool:
        client = await self.provider._get_client()
        metadata = await ProxyMetadataMiddleware(self.provider)._read_upstream(
            client, get_context()
        )
        return metadata is not None and SKILLS_EXTENSION_ID in (
            metadata.capabilities.extensions or {}
        )

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Client]:
        try:
            client = await self.provider._get_client()
            async with client:
                if client.protocol_version not in MODERN_PROTOCOL_VERSIONS:
                    raise MCPError(
                        code=METHOD_NOT_FOUND,
                        message="Skills require a modern MCP connection",
                    )
                metadata = await ProxyMetadataMiddleware(self.provider)._read_connected(
                    client
                )
                if metadata is None:
                    raise MCPError(
                        code=INTERNAL_ERROR,
                        message="Could not discover backend Skills support",
                    )
                if SKILLS_EXTENSION_ID not in (metadata.capabilities.extensions or {}):
                    raise MCPError(
                        code=METHOD_NOT_FOUND,
                        message="The backend does not support Skills",
                    )
                yield client
        except _PROXY_TRANSPORT_ERRORS as error:
            raise _proxy_upstream_error(error) from error

    def _expire_manifests(self) -> None:
        ttl = self.provider._cache_ttl or _DEFAULT_CACHE_TTL
        self._manifests = {
            uri: manifest
            for uri, manifest in self._manifests.items()
            if manifest.is_fresh(ttl)
        }

    def _remember(self, skill: Skill) -> None:
        if isinstance(skill.resources, list):
            self._manifests[(_get_auth_partition_key(), skill.uri)] = _CacheEntry(
                tuple(resource.uri for resource in skill.resources), time.monotonic()
            )
        else:
            self._manifests.pop((_get_auth_partition_key(), skill.uri), None)

    def _owns_skill_resource(self, uri: str) -> bool:
        self._expire_manifests()
        partition = _get_auth_partition_key()
        return any(
            uri in manifest.items
            for (owner, _), manifest in self._manifests.items()
            if owner == partition
        )

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> _Catalog:
        async with self._client() as client:
            meta = _forwardable_request_meta(get_context())

            async def fetch_page(cursor: str | None) -> ListSkillsResult:
                return await _client.list_skills(client, cursor=cursor, meta=meta)

            result = await _client.list_all_skills(fetch_page, name=client.name)
        self._expire_manifests()
        partition = _get_auth_partition_key()
        self._manifests = {
            key: value for key, value in self._manifests.items() if key[0] != partition
        }
        for skill in result.skills:
            self._remember(skill)
        return _Catalog(result, tuple(skill.uri for skill in result.skills))

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> _Lookup:
        async with self._client() as client:
            try:
                result = await _client.get_skill(
                    client, uri, meta=_forwardable_request_meta(get_context())
                )
            except MCPError as error:
                if error.error.code != INVALID_PARAMS:
                    raise
                return _Lookup()
        if result.skill.uri != uri:
            return _Lookup()
        self._expire_manifests()
        self._remember(result.skill)
        return _Lookup(matches=1, result=result)
