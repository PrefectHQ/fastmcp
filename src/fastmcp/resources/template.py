"""Resource template functionality."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from mcp.types import Annotations
from mcp.types import ResourceTemplate as MCPResourceTemplate
from pydantic import (
    Field,
    field_validator,
    validate_call,
)

from fastmcp.resources.resource import Resource
from fastmcp.server.dependencies import get_context
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.types import (
    find_kwarg_by_type,
    get_cached_typeadapter,
)


def build_regex(template: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\})", template)
    pattern = ""
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1]
            if name.endswith("*"):
                name = name[:-1]
                pattern += f"(?P<{name}>.+)"
            else:
                pattern += f"(?P<{name}>[^/]+)"
        else:
            pattern += re.escape(part)
    return re.compile(f"^{pattern}$")


def match_uri_template(uri: str, uri_template: str) -> dict[str, str] | None:
    regex = build_regex(uri_template)
    match = regex.match(uri)
    if match:
        return {k: unquote(v) for k, v in match.groupdict().items()}
    return None


class ResourceTemplate(FastMCPComponent):
    """A template for dynamically creating resources."""

    uri_template: str = Field(
        description="URI template with parameters (e.g. weather://{city}/current)"
    )
    mime_type: str = Field(
        default="text/plain", description="MIME type of the resource content"
    )
    parameters: dict[str, Any] = Field(
        description="JSON schema for function parameters"
    )
    annotations: Annotations | None = Field(
        default=None, description="Optional annotations about the resource's behavior"
    )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri_template={self.uri_template!r}, name={self.name!r}, description={self.description!r}, tags={self.tags})"

    def enable(self) -> None:
        super().enable()
        try:
            context = get_context()
            context._queue_resource_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

    def disable(self) -> None:
        super().disable()
        try:
            context = get_context()
            context._queue_resource_list_changed()  # type: ignore[private-use]
        except RuntimeError:
            pass  # No context available

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
    ) -> FunctionResourceTemplate:
        return FunctionResourceTemplate.from_function(
            fn=fn,
            uri_template=uri_template,
            name=name,
            title=title,
            description=description,
            mime_type=mime_type,
            tags=tags,
            enabled=enabled,
            annotations=annotations,
            meta=meta,
        )

    @field_validator("mime_type", mode="before")
    @classmethod
    def set_default_mime_type(cls, mime_type: str | None) -> str:
        """Set default MIME type if not provided."""
        if mime_type:
            return mime_type
        return "text/plain"

    def matches(self, uri: str) -> dict[str, Any] | None:
        """Check if URI matches template and extract parameters."""
        return match_uri_template(uri, self.uri_template)

    async def read(self, arguments: dict[str, Any]) -> str | bytes:
        """Read the resource content."""
        raise NotImplementedError(
            "Subclasses must implement read() or override create_resource()"
        )

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource from the template with the given parameters."""

        async def resource_read_fn() -> str | bytes:
            # Call function and check if result is a coroutine
            result = await self.read(arguments=params)
            return result

        return Resource.from_function(
            fn=resource_read_fn,
            uri=uri,
            name=self.name,
            description=self.description,
            mime_type=self.mime_type,
            tags=self.tags,
            enabled=self.enabled,
        )

    def to_mcp_template(
        self,
        *,
        include_fastmcp_meta: bool | None = None,
        **overrides: Any,
    ) -> MCPResourceTemplate:
        """Convert the resource template to an MCPResourceTemplate."""

        return MCPResourceTemplate(
            name=overrides.get("name", self.name),
            uriTemplate=overrides.get("uriTemplate", self.uri_template),
            description=overrides.get("description", self.description),
            mimeType=overrides.get("mimeType", self.mime_type),
            title=overrides.get("title", self.title),
            annotations=overrides.get("annotations", self.annotations),
            _meta=overrides.get(
                "_meta", self.get_meta(include_fastmcp_meta=include_fastmcp_meta)
            ),
        )

    @classmethod
    def from_mcp_template(cls, mcp_template: MCPResourceTemplate) -> ResourceTemplate:
        """Creates a FastMCP ResourceTemplate from a raw MCP ResourceTemplate object."""
        # Note: This creates a simple ResourceTemplate instance. For function-based templates,
        # the original function is lost, which is expected for remote templates.
        return cls(
            uri_template=mcp_template.uriTemplate,
            name=mcp_template.name,
            description=mcp_template.description,
            mime_type=mcp_template.mimeType or "text/plain",
            parameters={},  # Remote templates don't have local parameters
        )

    @property
    def key(self) -> str:
        """
        The key of the component. This is used for internal bookkeeping
        and may reflect e.g. prefixes or other identifiers. You should not depend on
        keys having a certain value, as the same tool loaded from different
        hierarchies of servers may have different keys.
        """
        return self._key or self.uri_template


class FunctionResourceTemplate(ResourceTemplate):
    """A template for dynamically creating resources."""

    fn: Callable[..., Any]

    async def read(self, arguments: dict[str, Any]) -> str | bytes:
        """Read the resource content."""
        from fastmcp.server.context import Context

        # Add context to parameters if needed
        kwargs = arguments.copy()
        context_kwarg = find_kwarg_by_type(self.fn, kwarg_type=Context)
        if context_kwarg and context_kwarg not in kwargs:
            kwargs[context_kwarg] = get_context()

        result = self.fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
    ) -> FunctionResourceTemplate:
        """Create a template from a function."""
        from fastmcp.server.context import Context

        func_name = name or getattr(fn, "__name__", None) or fn.__class__.__name__
        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        # Reject functions with *args
        # (**kwargs is allowed because the URI will define the parameter names)
        sig = inspect.signature(fn)
        for param in sig.parameters.values():
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                raise ValueError(
                    "Functions with *args are not supported as resource templates"
                )

        # Auto-detect context parameter if not provided

        context_kwarg = find_kwarg_by_type(fn, kwarg_type=Context)

        # Validate that URI params match function params
        uri_params = set(re.findall(r"{(\w+)(?:\*)?}", uri_template))
        if not uri_params:
            raise ValueError("URI template must contain at least one parameter")

        func_params = set(sig.parameters.keys())
        if context_kwarg:
            func_params.discard(context_kwarg)

        # get the parameters that are required
        required_params = {
            p
            for p in func_params
            if sig.parameters[p].default is inspect.Parameter.empty
            and sig.parameters[p].kind != inspect.Parameter.VAR_KEYWORD
            and p != context_kwarg
        }

        # Check if required parameters are a subset of the URI parameters
        if not required_params.issubset(uri_params):
            raise ValueError(
                f"Required function arguments {required_params} must be a subset of the URI parameters {uri_params}"
            )

        # Check if the URI parameters are a subset of the function parameters (skip if **kwargs present)
        if not any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in sig.parameters.values()
        ):
            if not uri_params.issubset(func_params):
                raise ValueError(
                    f"URI parameters {uri_params} must be a subset of the function arguments: {func_params}"
                )

        description = description or inspect.getdoc(fn)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        type_adapter = get_cached_typeadapter(fn)
        parameters = type_adapter.json_schema()

        # compress the schema
        prune_params = [context_kwarg] if context_kwarg else None
        parameters = compress_schema(parameters, prune_params=prune_params)

        # ensure the arguments are properly cast
        fn = validate_call(fn)

        return cls(
            uri_template=uri_template,
            name=func_name,
            title=title,
            description=description,
            mime_type=mime_type or "text/plain",
            fn=fn,
            parameters=parameters,
            tags=tags or set(),
            enabled=enabled if enabled is not None else True,
            annotations=annotations,
            meta=meta,
        )
