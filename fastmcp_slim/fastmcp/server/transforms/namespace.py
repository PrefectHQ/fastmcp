"""Namespace transform for prefixing component names."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, overload

import mcp.types

from fastmcp.server.transforms import (
    GetPromptNext,
    GetResourceNext,
    GetResourceTemplateNext,
    GetToolNext,
    Transform,
)
from fastmcp.tools.base import Tool, ToolResult
from fastmcp.utilities.versions import VersionSpec

if TYPE_CHECKING:
    from fastmcp.prompts.base import Prompt
    from fastmcp.resources.base import Resource
    from fastmcp.resources.template import ResourceTemplate
    from fastmcp.server.tasks.config import TaskMeta

# Pattern for matching URIs: protocol://path
_URI_PATTERN = re.compile(r"^([^:]+://)(.*?)$")


class _NamespacedResultTool(Tool):
    """Tool wrapper that keeps the wrapped tool's execution path intact."""

    _tool: Tool
    _namespace: Any

    def __init__(self, tool: Tool, namespace: Namespace, name: str) -> None:
        super().__init__(
            name=name,
            version=tool.version,
            title=tool.title,
            description=tool.description,
            icons=tool.icons,
            tags=tool.tags,
            meta=tool.meta,
            task_config=tool.task_config,
            parameters=tool.parameters,
            output_schema=tool.output_schema,
            annotations=tool.annotations,
            execution=tool.execution,
            serializer=tool.serializer,
            auth=tool.auth,
            timeout=tool.timeout,
        )
        self._tool = tool
        self._namespace = namespace

    @overload
    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: None = None,
    ) -> ToolResult: ...

    @overload
    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: TaskMeta | None = None,
    ) -> ToolResult | mcp.types.CreateTaskResult:
        result = await self._tool._run(arguments, task_meta=task_meta)
        return self._transform_result(result)

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return self._namespace._transform_tool_result(await self._tool.run(arguments))

    def register_with_docket(self, docket: Any) -> None:
        if not self.task_config.supports_tasks():
            return
        self._register_with_docket_as(docket, self.key)

    def _register_with_docket_as(self, docket: Any, key: str) -> None:
        fn = getattr(self._tool, "fn", None)
        if fn is not None:
            docket.register(fn, names=[key])
            return

        if isinstance(self._tool, _NamespacedResultTool):
            self._tool._register_with_docket_as(docket, key)
            return

        docket.register(self.run, names=[key])

    def get_span_attributes(self) -> dict[str, Any]:
        return self._tool.get_span_attributes() | {
            "fastmcp.component.key": self.key,
        }

    def _transform_result(
        self, result: ToolResult | mcp.types.CreateTaskResult
    ) -> ToolResult | mcp.types.CreateTaskResult:
        if isinstance(result, mcp.types.CreateTaskResult):
            return result
        return self._namespace._transform_tool_result(result)


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
        return [self._transform_tool(t, self._transform_name(t.name)) for t in tools]

    async def get_tool(
        self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None
    ) -> Tool | None:
        """Get tool by namespaced name."""
        original = self._reverse_name(name)
        if original is None:
            return None
        tool = await call_next(original, version=version)
        if tool:
            return self._transform_tool(tool, name)
        return None

    def _transform_tool(self, tool: Tool, name: str) -> Tool:
        """Prefix a tool name and project ResourceLink result URIs."""
        return _NamespacedResultTool(tool, namespace=self, name=name)

    def _transform_tool_result(self, result: ToolResult) -> ToolResult:
        content = [self._transform_content_block(block) for block in result.content]
        if content == result.content:
            return result
        return ToolResult(
            content=content,
            structured_content=result.structured_content,
            meta=result.meta,
        )

    def _transform_content_block(
        self, block: mcp.types.ContentBlock
    ) -> mcp.types.ContentBlock:
        if not isinstance(block, mcp.types.ResourceLink):
            return block
        return block.model_copy(
            update={"uri": mcp.types.AnyUrl(self._transform_uri(str(block.uri)))}
        )

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
