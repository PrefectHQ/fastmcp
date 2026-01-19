"""Resource template functionality."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, overload
from urllib.parse import parse_qs, unquote

import mcp.types
from mcp.types import Annotations, Icon

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution
from mcp.types import ResourceTemplate as SDKResourceTemplate
from pydantic import (
    Field,
    field_validator,
    validate_call,
)

from fastmcp.resources.resource import Resource, ResourceResult
from fastmcp.server.dependencies import (
    transform_context_annotations,
    without_injected_parameters,
)
from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.tools.tool import AuthCheckCallable
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
                params[name] = parsed_query[name][0]

    return params


class ResourceTemplate(FastMCPComponent):
    """A template for dynamically creating resources."""

    KEY_PREFIX: ClassVar[str] = "template"

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
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = Field(
        default=None,
        description="Authorization checks for this resource template",
        exclude=True,
    )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(uri_template={self.uri_template!r}, name={self.name!r}, description={self.description!r}, tags={self.tags})"

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> FunctionResourceTemplate:
        return FunctionResourceTemplate.from_function(
            fn=fn,
            uri_template=uri_template,
            name=name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type,
            tags=tags,
            annotations=annotations,
            meta=meta,
            task=task,
            auth=auth,
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

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
        """Read the resource content."""
        raise NotImplementedError(
            "Subclasses must implement read() or override create_resource()"
        )

    def convert_result(self, raw_value: Any) -> ResourceResult:
        """Convert a raw result to ResourceResult.

        This is used in two contexts:
        1. In _read() to convert user function return values to ResourceResult
        2. In tasks_result_handler() to convert Docket task results to ResourceResult

        Handles ResourceResult passthrough and converts raw values using
        ResourceResult's normalization.
        """
        if isinstance(raw_value, ResourceResult):
            return raw_value

        # ResourceResult.__init__ handles all normalization
        return ResourceResult(raw_value)

    @overload
    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: None = None
    ) -> ResourceResult: ...

    @overload
    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: TaskMeta
    ) -> mcp.types.CreateTaskResult: ...

    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: TaskMeta | None = None
    ) -> ResourceResult | mcp.types.CreateTaskResult:
        """Server entry point that handles task routing.

        This allows ANY ResourceTemplate subclass to support background execution
        by setting task_config.mode to "supported" or "required". The server calls
        this method instead of create_resource()/read() directly.

        Args:
            uri: The concrete URI being read
            params: Template parameters extracted from the URI
            task_meta: If provided, execute as a background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return ResourceResult.

        Returns:
            ResourceResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Subclasses can override this to customize task routing behavior.
        For example, FastMCPProviderResourceTemplate overrides to delegate to child
        middleware without submitting to Docket.
        """
        from fastmcp.server.tasks.routing import check_background_task

        task_result = await check_background_task(
            component=self, task_type="template", arguments=params, task_meta=task_meta
        )
        if task_result:
            return task_result

        # Synchronous execution - create resource and read directly
        # Call resource.read() not resource._read() to avoid task routing on ephemeral resource
        resource = await self.create_resource(uri, params)
        result = await resource.read()
        return self.convert_result(result)

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
            _meta=overrides.get(  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                "_meta", self.get_meta()
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
        """The globally unique lookup key for this template."""
        base_key = self.make_key(self.uri_template)
        return f"{base_key}@{self.version or ''}"

    def register_with_docket(self, docket: Docket) -> None:
        """Register this template with docket for background execution."""
        if not self.task_config.supports_tasks():
            return
        docket.register(self.read, names=[self.key])

    async def add_to_docket(  # type: ignore[override]
        self,
        docket: Docket,
        params: dict[str, Any],
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this template for background execution via docket.

        Args:
            docket: The Docket instance
            params: Template parameters
            fn_key: Function lookup key in Docket registry (defaults to self.key)
            task_key: Redis storage key for the result
            **kwargs: Additional kwargs passed to docket.add()
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)(params)

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.component.type": "resource_template",
            "fastmcp.provider.type": "LocalProvider",
        }


class FunctionResourceTemplate(ResourceTemplate):
    """A template for dynamically creating resources."""

    fn: Callable[..., Any]

    @overload
    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: None = None
    ) -> ResourceResult: ...

    @overload
    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: TaskMeta
    ) -> mcp.types.CreateTaskResult: ...

    async def _read(
        self, uri: str, params: dict[str, Any], task_meta: TaskMeta | None = None
    ) -> ResourceResult | mcp.types.CreateTaskResult:
        """Optimized server entry point that skips ephemeral resource creation.

        For FunctionResourceTemplate, we can call read() directly instead of
        creating a temporary resource, which is more efficient.

        Args:
            uri: The concrete URI being read
            params: Template parameters extracted from the URI
            task_meta: If provided, execute as a background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return ResourceResult.

        Returns:
            ResourceResult when task_meta is None.
            CreateTaskResult when task_meta is provided.
        """
        from fastmcp.server.tasks.routing import check_background_task

        task_result = await check_background_task(
            component=self, task_type="template", arguments=params, task_meta=task_meta
        )
        if task_result:
            return task_result

        # Synchronous execution - call read() directly, skip resource creation
        result = await self.read(arguments=params)
        return self.convert_result(result)

    async def create_resource(self, uri: str, params: dict[str, Any]) -> Resource:
        """Create a resource from the template with the given parameters."""

        async def resource_read_fn() -> str | bytes | ResourceResult:
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
            task=self.task_config,
            auth=self.auth,
        )

    async def read(self, arguments: dict[str, Any]) -> str | bytes | ResourceResult:
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

    def register_with_docket(self, docket: Docket) -> None:
        """Register this template with docket for background execution.

        FunctionResourceTemplate registers the underlying function, which has the
        user's Depends parameters for docket to resolve.
        """
        if not self.task_config.supports_tasks():
            return
        docket.register(self.fn, names=[self.key])

    async def add_to_docket(
        self,
        docket: Docket,
        params: dict[str, Any],
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this template for background execution via docket.

        FunctionResourceTemplate splats the params dict since .fn expects **kwargs.

        Args:
            docket: The Docket instance
            params: Template parameters
            fn_key: Function lookup key in Docket registry (defaults to self.key)
            task_key: Redis storage key for the result
            **kwargs: Additional kwargs passed to docket.add()
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)(**params)

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        uri_template: str,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        mime_type: str | None = None,
        tags: set[str] | None = None,
        annotations: Annotations | None = None,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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

        # Transform Context type annotations to Depends() for unified DI
        fn = transform_context_annotations(fn)

        wrapper_fn = without_injected_parameters(fn)
        type_adapter = get_cached_typeadapter(wrapper_fn)
        parameters = type_adapter.json_schema()
        parameters = compress_schema(parameters, prune_titles=True)

        # Use validate_call on wrapper for runtime type coercion
        fn = validate_call(wrapper_fn)

        return cls(
            uri_template=uri_template,
            name=func_name,
            version=str(version) if version is not None else None,
            title=title,
            description=description,
            icons=icons,
            mime_type=mime_type or "text/plain",
            fn=fn,
            parameters=parameters,
            tags=tags or set(),
            annotations=annotations,
            meta=meta,
            task_config=task_config,
            auth=auth,
        )
