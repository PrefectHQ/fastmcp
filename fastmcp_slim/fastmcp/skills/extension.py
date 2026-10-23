"""FastMCP server implementation of the MCP Skills extension."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import mcp_types
from mcp import MCPError
from mcp.server.context import ServerRequestContext
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.mixins.mcp_operations import _apply_pagination
from fastmcp.server.providers.base import Provider
from fastmcp.skills._composition import (
    _ServerSkills,
    _transformed,
    _unsupported_transforms,
)
from fastmcp.skills._constants import SKILLS_EXTENSION_ID
from fastmcp.skills._resources import file_mime_type
from fastmcp.skills._source import _SkillSource
from fastmcp.skills.models import (
    GetSkillParams,
    GetSkillResult,
    ListSkillsParams,
    ListSkillsResult,
    ReadResourceDirectoryParams,
    ReadResourceDirectoryResult,
    Skill,
)

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

_SKILLS_METHOD_VERSIONS = frozenset(MODERN_PROTOCOL_VERSIONS)
_DIRECTORY_MIME_TYPE = "inode/directory"


class SkillsExtension(ServerExtension):
    """Expose caller-scoped and proxied skill sources through SEP-2640 methods.

    Args:
        providers: Skill sources registered directly on the same server.
        directory_read: Advertise `directoryRead` and serve
            `resources/directory/read` for directories within skills whose
            entries carry a static manifest.
    """

    identifier = SKILLS_EXTENSION_ID

    def __init__(
        self, *, providers: Sequence[Provider], directory_read: bool = False
    ) -> None:
        if not providers:
            raise ValueError("SkillsExtension requires at least one provider")
        if len({id(provider) for provider in providers}) != len(providers):
            raise ValueError("SkillsExtension providers must be unique")
        self._providers = tuple(providers)
        self._directory_read = directory_read

    def _bind(self, server: FastMCP) -> None:
        self._validate_configuration(server)
        super()._bind(server)

    def _validate_configuration(self, server: FastMCP) -> tuple[_SkillSource, ...]:
        if unsupported := _unsupported_transforms(server):
            raise ValueError(
                "SkillsExtension does not support server-level "
                f"{type(unsupported[0]).__name__} transforms"
            )
        sources = []
        for provider in self._providers:
            if not any(registered is provider for registered in server.providers):
                raise ValueError(
                    "SkillsExtension providers must be registered directly on the "
                    "same FastMCP server before the extension"
                )
            if unsupported := _unsupported_transforms(provider):
                raise ValueError(
                    "SkillsExtension does not support providers with "
                    f"{type(unsupported[0]).__name__} transforms"
                )
            if not isinstance(provider, _SkillSource):
                raise TypeError(
                    f"{type(provider).__name__} is not a SkillsExtension source; "
                    "subclass SkillFilesProvider to publish skills from loaded files"
                )
            provider._validate_skill_source()
            source = _transformed(provider, provider)
            assert source is not None
            sources.append(source)
        return tuple(sources)

    def _registered_sources(self) -> tuple[_SkillSource, ...]:
        """This server's own sources, which mounted parents forward as well."""
        return self._validate_configuration(self.server)

    @property
    def _source(self) -> _SkillSource | None:
        # The server validates this extension's own sources while collecting them.
        source = self.server._get_skills_source()
        return _ServerSkills(source, self.server) if source is not None else None

    def _require_source(self) -> _SkillSource:
        source = self._source
        if source is None:
            raise MCPError(code=METHOD_NOT_FOUND, message="Skills are not supported")
        return source

    async def _requires_live_resource(self, uri: str) -> bool:
        """Keep manifest-backed resources coherent with current file bytes."""
        source = self._source
        return source is not None and await source._owns_skill_resource(uri)

    def settings(self) -> dict[str, Any]:
        return {"directoryRead": True} if self._directory_read else {}

    def methods(self) -> Sequence[MethodBinding]:
        directory: tuple[MethodBinding, ...] = (
            (
                MethodBinding(
                    method="resources/directory/read",
                    params_type=ReadResourceDirectoryParams,
                    handler=self._handle_read_directory,
                    protocol_versions=_SKILLS_METHOD_VERSIONS,
                ),
            )
            if self._directory_read
            else ()
        )
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
            *directory,
        )

    async def _handle_list(
        self, context: ServerRequestContext[Any, Any], params: ListSkillsParams
    ) -> ListSkillsResult:
        catalog = await self._require_source()._list_skill_entries(context)
        page, next_cursor = _apply_pagination(
            catalog.skills, params.cursor, self.server._list_page_size
        )
        return ListSkillsResult(skills=page, next_cursor=next_cursor)

    async def _handle_get(
        self, context: ServerRequestContext[Any, Any], params: GetSkillParams
    ) -> GetSkillResult:
        lookup = await self._require_source()._get_skill_entry(params.uri, context)
        if (
            lookup.matches != 1
            or lookup.skill is None
            or lookup.skill.uri != params.uri
        ):
            raise MCPError(
                code=INVALID_PARAMS, message=f"Unknown skill URI: {params.uri}"
            )
        return GetSkillResult(skill=lookup.skill)

    async def _handle_read_directory(
        self,
        context: ServerRequestContext[Any, Any],
        params: ReadResourceDirectoryParams,
    ) -> ReadResourceDirectoryResult:
        catalog = await self._require_source()._list_skill_entries(context)
        children = _directory_children(params.uri, catalog.skills)
        if children is None:
            raise MCPError(
                code=INVALID_PARAMS, message=f"Unknown directory: {params.uri}"
            )
        page, next_cursor = _apply_pagination(
            children, params.cursor, self.server._list_page_size
        )
        return ReadResourceDirectoryResult(resources=page, next_cursor=next_cursor)


def _directory_children(
    uri: str, skills: Sequence[Skill]
) -> list[mcp_types.Resource] | None:
    """List a directory's direct children from the visible skills' manifests.

    Directories are the levels above each manifest file, from the scheme
    authority down to the file's parent. Skills with dynamic resources have no
    manifest to answer from, so only their path above the skill root is known.
    Returns `None` when no visible skill contains the directory.
    """
    prefix = f"{uri}/"
    children: dict[str, mcp_types.Resource] = {}
    found = False
    for skill in skills:
        dynamic = not isinstance(skill.resources, list)
        files = (
            [(skill.uri, None)]
            if isinstance(skill.resources, str)
            else [(resource.uri, resource.size) for resource in skill.resources]
        )
        for file_uri, size in files:
            if not file_uri.startswith(prefix):
                continue
            rest = file_uri[len(prefix) :]
            if dynamic and "/" not in rest:
                continue
            found = True
            segment, separator, _ = rest.partition("/")
            child_uri = prefix + segment
            name = unquote(segment)
            if separator:
                children[child_uri] = mcp_types.Resource(
                    uri=child_uri, name=name, mime_type=_DIRECTORY_MIME_TYPE
                )
            else:
                children[child_uri] = mcp_types.Resource(
                    uri=child_uri,
                    name=name,
                    mime_type=file_mime_type(name),
                    size=size,
                )
    if not found:
        return None
    return [children[child] for child in sorted(children)]


class _ProxySkillsExtension(SkillsExtension):
    """Installed on every server; advertised only when a proxied backend has Skills."""

    def __init__(self) -> None:
        self._providers = ()
        self._directory_read = False

    def _validate_configuration(self, server: FastMCP) -> tuple[_SkillSource, ...]:
        return ()

    async def _is_supported(self, context: ServerRequestContext[Any, Any]) -> bool:
        source = self._source
        return source is not None and await source._is_supported(context)
