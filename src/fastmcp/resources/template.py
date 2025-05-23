"""Resource template functionality."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import unquote

from mcp.types import ResourceTemplate as MCPResourceTemplate
from pydantic import (
    AnyUrl,
    BaseModel,
    BeforeValidator,
    Field,
    field_validator,
    validate_call,
)

from fastmcp.resources.types import FunctionResource, Resource
from fastmcp.server.dependencies import get_context
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.types import (
    _convert_set_defaults,
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


class MyModel(BaseModel):
    key: str
    value: int


class ResourceTemplate(BaseModel):
    """A template for dynamically creating resources."""

    uri_template: str = Field(
        description="URI template with parameters (e.g. weather://{city}/current)"
    )
    name: str = Field(description="Name of the resource")
    description: str | None = Field(description="Description of what the resource does")
    tags: Annotated[set[str], BeforeValidator(_convert_set_defaults)] = Field(
        default_factory=set, description="Tags for the resource"
    )
    mime_type: str = Field(
        default="text/plain", description="MIME type of the resource content"
    )
    fn: Callable[..., Any]
    parameters: dict[str, Any] = Field(
        description="JSON schema for function parameters"
    )

    @field_validator("mime_type", mode="before")
    @classmethod
    def set_default_mime_type(cls, mime_type: str | None) -> str:
        """Set default MIME type if not provided."""
        if mime_type:
            return mime_type
        return "text/plain"

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
    ) -> ResourceTemplate:
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

        description = description or fn.__doc__ or ""

        if not inspect.isroutine(fn):
            fn = fn.__call__

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
            description=description,
            mime_type=mime_type or "text/plain",
            fn=fn,
            parameters=parameters,
            tags=tags or set(),
        )

    def matches(self, uri: str) -> dict[str, Any] | None:
        """Check if URI matches template and extract parameters."""
        return match_uri_template(uri, self.uri_template)

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource from the template with the given parameters."""
        from fastmcp.server.context import Context

        # Add context to parameters if needed
        kwargs = params.copy()
        context_kwarg = find_kwarg_by_type(self.fn, kwarg_type=Context)
        if context_kwarg and context_kwarg not in kwargs:
            kwargs[context_kwarg] = get_context()

        async def resource_read_fn() -> str | bytes:
            # Call function and check if result is a coroutine
            result = self.fn(**kwargs)
            if inspect.iscoroutine(result):
                result = await result
            return result

        return FunctionResource(
            uri=AnyUrl(uri),  # Explicitly convert to AnyUrl
            name=self.name,
            description=self.description,
            mime_type=self.mime_type,
            fn=resource_read_fn,
            tags=self.tags,
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ResourceTemplate):
            return False
        return self.model_dump() == other.model_dump()

    def to_mcp_template(self, **overrides: Any) -> MCPResourceTemplate:
        """Convert the resource template to an MCPResourceTemplate."""
        kwargs = {
            "uriTemplate": self.uri_template,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }
        return MCPResourceTemplate(**kwargs | overrides)
