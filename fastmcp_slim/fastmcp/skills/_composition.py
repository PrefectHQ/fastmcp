"""Skills discovery for servers mounted with `FastMCP.mount()`."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from mcp.server.context import ServerRequestContext

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.base import Provider
from fastmcp.server.providers.fastmcp_provider import (
    FastMCPProvider,
    FastMCPProviderResource,
    FastMCPProviderResourceTemplate,
)
from fastmcp.server.providers.wrapped_provider import _WrappedProvider
from fastmcp.server.transforms import Transform
from fastmcp.skills.models import MCPSkill

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP
    from fastmcp.skills.extension import SkillsExtension


def _unwrap(provider: Provider) -> tuple[Provider, list[Transform]]:
    """Return the innermost provider and its transforms, innermost first."""
    layers: list[Provider] = []
    while isinstance(provider, _WrappedProvider):
        layers.append(provider)
        provider = provider._inner
    transforms = list(provider.transforms)
    for layer in reversed(layers):
        transforms.extend(layer._transforms)
    return provider, transforms


class _MountedSkills:
    """A skill source for a mounted server that has its own `SkillsExtension`.

    The child's entries are rewritten to the URIs the parent serves them at.
    An entry is kept only when the parent routes every file URI back to the
    same file on the child and the caller passes the parent's access checks,
    so a transform, shadowing resource, or middleware that hides a file also
    hides the skill.
    """

    def __init__(
        self,
        mount: Provider,
        server: FastMCP,
        extension: SkillsExtension,
        transforms: Sequence[Transform],
    ) -> None:
        self._mount = mount
        self._server = server
        self._extension = extension
        self._transforms = tuple(transforms)

    def _to_parent(self, uri: str) -> str:
        for transform in self._transforms:
            forward: Callable[[str], str] | None = getattr(
                transform, "_transform_uri", None
            )
            if forward is not None:
                uri = forward(uri)
        return uri

    def _to_child(self, uri: str) -> str | None:
        for transform in reversed(self._transforms):
            reverse: Callable[[str], str | None] | None = getattr(
                transform, "_reverse_uri", None
            )
            if reverse is not None:
                reversed_uri = reverse(uri)
                if reversed_uri is None:
                    return None
                uri = reversed_uri
        return uri

    def _validate_skill_source(self) -> None:
        return None

    def _owns_skill_resource(self, uri: str) -> bool:
        child_uri = self._to_child(uri)
        return child_uri is not None and self._extension._requires_live_resource(
            child_uri
        )

    async def _skill_uris(self, uri: str | None = None) -> Sequence[str]:
        child_uri = None if uri is None else self._to_child(uri)
        if uri is not None and child_uri is None:
            return []
        identities = await self._extension._identities(child_uri)
        return [self._to_parent(identity) for identity in identities]

    async def _list_skill_entries(
        self, context: ServerRequestContext[Any, Any]
    ) -> Sequence[MCPSkill]:
        entries = await self._extension._list_entries(context)
        mapped = [await self._map(entry) for entry in entries]
        return [entry for entry in mapped if entry is not None]

    async def _get_skill_entry(
        self, uri: str, context: ServerRequestContext[Any, Any]
    ) -> MCPSkill | None:
        child_uri = self._to_child(uri)
        if child_uri is None:
            return None
        entry = await self._extension._get_entry(child_uri, context)
        return None if entry is None else await self._map(entry)

    async def _map(self, entry: MCPSkill) -> MCPSkill | None:
        if entry.resources == "dynamic":
            return None
        resources = []
        for record in entry.resources:
            uri = self._to_parent(record.uri)
            if not await self._routes_to_child(uri, record.uri):
                return None
            resources.append(record.model_copy(update={"uri": uri}))
        return entry.model_copy(
            update={"uri": self._to_parent(entry.uri), "resources": resources}
        )

    async def _routes_to_child(self, uri: str, child_uri: str) -> bool:
        parent = get_context().fastmcp
        resource, exists = await parent._lookup_resource(uri)
        if resource is None:
            if exists:
                # A hidden concrete resource blocks the template, as in reads.
                return False
            template = await parent.get_resource_template(uri)
            if not isinstance(template, FastMCPProviderResourceTemplate):
                return False
            params = template.matches(uri)
            if params is None:
                return False
            resource = await template.create_resource(uri, params)
        if not (
            isinstance(resource, FastMCPProviderResource)
            and resource._server is self._server
            and resource._original_uri == child_uri
        ):
            return False
        try:
            await parent._check_resource_access(uri)
        except AuthorizationError:
            return False
        return True


def _mounted_source(provider: Provider) -> _MountedSkills | None:
    from fastmcp.skills._constants import SKILLS_EXTENSION_ID
    from fastmcp.skills.extension import SkillsExtension

    inner, transforms = _unwrap(provider)
    if not isinstance(inner, FastMCPProvider):
        return None
    extension = inner.server._extensions.get(SKILLS_EXTENSION_ID)
    if not isinstance(extension, SkillsExtension):
        return None
    return _MountedSkills(inner, inner.server, extension, transforms)
