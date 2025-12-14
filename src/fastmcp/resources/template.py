"""Resource template functionality."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import parse_qs, unquote

from mcp.types import Annotations, Icon
from mcp.types import ResourceTemplate as SDKResourceTemplate
from pydantic import (
    Field,
    field_validator,
    validate_call,
)

from fastmcp.resources.resource import Resource
from fastmcp.server.dependencies import get_context, without_injected_parameters
from fastmcp.server.tasks.config import TaskConfig
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.types import get_cached_typeadapter


def extract_query_params(uri_template: str) -> set[str]:
    """Extract query parameter names from RFC 6570 `{?param1,param2}` syntax."""
    match = re.search(r"\{\?([^}]+)\}", uri_template)
    if match:
        return {p.strip() for p in match.group(1).split(",")}
    return set()


def build_regex(template: str) -> re.Pattern:
    """Build regex pattern for URI template, handling RFC 6570 syntax.

    Supports:
    - `{var}` - simple path parameter
    - `{var*}` - wildcard path parameter (captures multiple segments)
    - `{?var1,var2}` - query parameters (ignored in path matching)
    """
    # Remove query parameter syntax for path matching
    template_without_query = re.sub(r"\{\?[^}]+\}", "", template)

    parts = re.split(r"(\{[^}]+\})", template_without_query)
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
    """Match URI against template and extract both path and query parameters.

    Supports RFC 6570 URI templates:
    - Path params: `{var}`, `{var*}`
    - Query params: `{?var1,var2}`
    """
    # Split URI into path and query parts
    uri_path, _, query_string = uri.partition("?")

    # Match path parameters
    regex = build_regex(uri_template)
    match = regex.match(uri_path)
    if not match:
        return None

    params = {k: unquote(v) for k, v in match.groupdict().items()}

    # Extract query parameters if present in URI and template
    if query_string:
        query_param_names = extract_query_params(uri_template)
        parsed_query = parse_qs(query_string)

        for name in query_param_names:
            if name in parsed_query:
                # Take first value if multiple provided
                params[name] = parsed_query[name][0]  # type: ignore[index]

    return params


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
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionResourceTemplate:
        return FunctionResourceTemplate.from_function(
            fn=fn,
            uri_template=uri_template,
            name=name,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            enabled=enabled,
            annotations=annotations,
            meta=meta,
            task=task,
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
        """Create a resource from the template with the given parameters.

        The base implementation does not support background tasks.
        Use FunctionResourceTemplate for task support.
        """
        raise NotImplementedError(
            "Subclasses must implement create_resource(). "
            "Use FunctionResourceTemplate for task support."
        )

    def to_mcp_template(
        self,
        *,
        include_fastmcp_meta: bool | None = None,
        **overrides: Any,
    ) -> SDKResourceTemplate:
        """Convert the resource template to an SDKResourceTemplate."""

        return SDKResourceTemplate(
            name=overrides.get("name", self.name),
            uriTemplate=overrides.get("uriTemplate", self.uri_template),
            description=overrides.get("description", self.description),
            mimeType=overrides.get("mimeType", self.mime_type),
            title=overrides.get("title", self.title),
            icons=overrides.get("icons", self.icons),
            annotations=overrides.get("annotations", self.annotations),
            _meta=overrides.get(
                "_meta", self.get_meta(include_fastmcp_meta=include_fastmcp_meta)
            ),
        )

    @classmethod
    def from_mcp_template(cls, mcp_template: SDKResourceTemplate) -> ResourceTemplate:
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
    task_config: Annotated[
        TaskConfig,
        Field(description="Background task execution configuration (SEP-1686)."),
    ] = Field(default_factory=lambda: TaskConfig(mode="forbidden"))

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
            task=self.task_config,
        )

    async def read(self, arguments: dict[str, Any]) -> str | bytes:
        """Read the resource content."""
        # Type coercion for query parameters (which arrive as strings)
        kwargs = arguments.copy()
        sig = inspect.signature(self.fn)
        for param_name, param_value in list(kwargs.items()):
            if param_name in sig.parameters and isinstance(param_value, str):
                param = sig.parameters[param_name]
                annotation = param.annotation

                if annotation is inspect.Parameter.empty or annotation is str:
                    continue

                try:
                    if annotation is int:
                        kwargs[param_name] = int(param_value)
                    elif annotation is float:
                        kwargs[param_name] = float(param_value)
                    elif annotation is bool:
                        kwargs[param_name] = param_value.lower() in ("true", "1", "yes")
                except (ValueError, AttributeError):
                    pass

        # self.fn is wrapped by without_injected_parameters which handles
        # dependency resolution internally, so we call it directly
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
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        enabled: bool | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
    ) -> FunctionResourceTemplate:
        """Create a template from a function."""

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

        # Extract path and query parameters from URI template
        path_params = set(re.findall(r"{(\w+)(?:\*)?}", uri_template))
        query_params = extract_query_params(uri_template)
        all_uri_params = path_params | query_params

        if not all_uri_params:
            raise ValueError("URI template must contain at least one parameter")

        # Use wrapper to get user-facing parameters (excludes injected params)
        wrapper_fn = without_injected_parameters(fn)
        user_sig = inspect.signature(wrapper_fn)
        func_params = set(user_sig.parameters.keys())

        # Get required and optional function parameters
        required_params = {
            p
            for p in func_params
            if user_sig.parameters[p].default is inspect.Parameter.empty
            and user_sig.parameters[p].kind != inspect.Parameter.VAR_KEYWORD
        }
        optional_params = {
            p
            for p in func_params
            if user_sig.parameters[p].default is not inspect.Parameter.empty
            and user_sig.parameters[p].kind != inspect.Parameter.VAR_KEYWORD
        }

        # Validate RFC 6570 query parameters
        # Query params must be optional (have defaults)
        if query_params:
            invalid_query_params = query_params - optional_params
            if invalid_query_params:
                raise ValueError(
                    f"Query parameters {invalid_query_params} must be optional function parameters with default values"
                )

        # Check if required parameters are a subset of the path parameters
        if not required_params.issubset(path_params):
            raise ValueError(
                f"Required function arguments {required_params} must be a subset of the URI path parameters {path_params}"
            )

        # Check if all URI parameters are valid function parameters (skip if **kwargs present)
        if not any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in sig.parameters.values()
        ):
            if not all_uri_params.issubset(func_params):
                raise ValueError(
                    f"URI parameters {all_uri_params} must be a subset of the function arguments: {func_params}"
                )

        description = description or inspect.getdoc(fn)

        # Normalize task to TaskConfig and validate
        if task is None:
            task_config = TaskConfig(mode="forbidden")
        elif isinstance(task, bool):
            task_config = TaskConfig.from_bool(task)
        else:
            task_config = task
        task_config.validate_function(fn, func_name)

        # if the fn is a callable class, we need to get the __call__ method from here out
        if not inspect.isroutine(fn):
            fn = fn.__call__
        # if the fn is a staticmethod, we need to work with the underlying function
        if isinstance(fn, staticmethod):
            fn = fn.__func__

        wrapper_fn = without_injected_parameters(fn)
        type_adapter = get_cached_typeadapter(wrapper_fn)
        parameters = type_adapter.json_schema()
        parameters = compress_schema(parameters, prune_titles=True)

        # Use validate_call on wrapper for runtime type coercion
        fn = validate_call(wrapper_fn)

        return cls(
            uri_template=uri_template,
            name=func_name,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type or "text/plain",
            fn=fn,
            parameters=parameters,
            tags=tags or set(),
            enabled=enabled if enabled is not None else True,
            annotations=annotations,
            meta=meta,
            task_config=task_config,
        )
