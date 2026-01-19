"""Namespace transform for prefixing component names."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from fastmcp.server.transforms import (
    GetPromptNext,
    GetResourceNext,
    GetResourceTemplateNext,
    GetToolNext,
    Transform,
)
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.prompts.prompt import Prompt
    from fastmcp.resources.resource import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.tools.tool import Tool

# Pattern for matching URIs: protocol://path
_URI_PATTERN = re.compile(r"^([^:]+://)(.*?)$")


class Namespace(Transform):
    """Prefixes component names with a namespace.

    - Tools: name → namespace_name
    - Prompts: name → namespace_name
    - Resources: protocol://path → protocol://namespace/path
    - Resource Templates: same as resources

    Example:
        ```python
        transform = Namespace("math")
        # Tool "add" becomes "math_add"
        # Resource "file://data.txt" becomes "file://math/data.txt"
        ```
    """

    def __init__(self, prefix: str) -> None:
        """Initialize Namespace transform.

        Args:
            prefix: The namespace prefix to apply.
        """
        self._prefix = prefix
        self._name_prefix = f"{prefix}_"

    def __repr__(self) -> str:
        return f"Namespace({self._prefix!r})"

    # -------------------------------------------------------------------------
    # Name transformation helpers
    # -------------------------------------------------------------------------

    def _transform_name(self, name: str) -> str:
        """Apply namespace prefix to a name."""
        return f"{self._name_prefix}{name}"

    def _reverse_name(self, name: str) -> str | None:
        """Remove namespace prefix from a name, or None if no match."""
        if name.startswith(self._name_prefix):
            return name[len(self._name_prefix) :]
        return None

    # -------------------------------------------------------------------------
    # URI transformation helpers
    # -------------------------------------------------------------------------

    def _transform_uri(self, uri: str) -> str:
        """Apply namespace to a URI: protocol://path → protocol://namespace/path."""
        match = _URI_PATTERN.match(uri)
        if match:
            protocol, path = match.groups()
            return f"{protocol}{self._prefix}/{path}"
        return uri

    def _reverse_uri(self, uri: str) -> str | None:
        """Remove namespace from a URI, or None if no match."""
        match = _URI_PATTERN.match(uri)
        if match:
            protocol, path = match.groups()
            prefix = f"{self._prefix}/"
            if path.startswith(prefix):
                return f"{protocol}{path[len(prefix) :]}"
            return None
        return None

    # -------------------------------------------------------------------------
    # Tools
    # -------------------------------------------------------------------------

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Prefix tool names with namespace."""
        return [
            t.model_copy(update={"name": self._transform_name(t.name)}) for t in tools
        ]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool by namespaced name."""
        original = self._reverse_name(name)
        if original is None:
            return None
        tool = await call_next(original, version=version)
        if tool:
            return tool.model_copy(update={"name": name})
        return None

    # -------------------------------------------------------------------------
    # Resources
    # -------------------------------------------------------------------------

    async def list_resources(self, resources: Sequence[Resource]) -> Sequence[Resource]:
        """Add namespace path segment to resource URIs."""
        return [
            r.model_copy(update={"uri": self._transform_uri(str(r.uri))})
            for r in resources
        ]

    async def get_resource(
        self,
        uri: str,
        call_next: GetResourceNext,
        *,
        version: VersionSpec | None = None,
    ) -> Resource | None:
        """Get resource by namespaced URI."""
        original = self._reverse_uri(uri)
        if original is None:
            return None
        resource = await call_next(original, version=version)
        if resource:
            return resource.model_copy(update={"uri": uri})
        return None

    # -------------------------------------------------------------------------
    # Resource Templates
    # -------------------------------------------------------------------------

    async def list_resource_templates(
        self, templates: Sequence[ResourceTemplate]
    ) -> Sequence[ResourceTemplate]:
        """Add namespace path segment to template URIs."""
        return [
            t.model_copy(update={"uri_template": self._transform_uri(t.uri_template)})
            for t in templates
        ]

    async def get_resource_template(
        self,
        uri: str,
        call_next: GetResourceTemplateNext,
        *,
        version: VersionSpec | None = None,
    ) -> ResourceTemplate | None:
        """Get resource template by namespaced URI."""
        original = self._reverse_uri(uri)
        if original is None:
            return None
        template = await call_next(original, version=version)
        if template:
            return template.model_copy(
                update={"uri_template": self._transform_uri(template.uri_template)}
            )
        return None

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    async def list_prompts(self, prompts: Sequence[Prompt]) -> Sequence[Prompt]:
        """Prefix prompt names with namespace."""
        return [
            p.model_copy(update={"name": self._transform_name(p.name)}) for p in prompts
        ]

    async def get_prompt(
        self, name: str, call_next: GetPromptNext, *, version: VersionSpec | None = None
    ) -> Prompt | None:
        """Get prompt by namespaced name."""
        original = self._reverse_name(name)
        if original is None:
            return None
        prompt = await call_next(original, version=version)
        if prompt:
            return prompt.model_copy(update={"name": name})
        return None
