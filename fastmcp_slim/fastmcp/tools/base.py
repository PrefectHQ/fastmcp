from __future__ import annotations

from collections.abc import Callable
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    overload,
)

import mcp_types
import pydantic_core
from mcp.shared.tool_name_validation import validate_and_warn_tool_name
from mcp_types import (
    CallToolResult,
    ContentBlock,
    Icon,
    TextContent,
    ToolAnnotations,
    ToolExecution,
)
from mcp_types import Tool as MCPTool
from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from fastmcp.utilities.authorization import AuthCheck
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.tasks import TaskConfig, TaskMeta
from fastmcp.utilities.types import (
    Audio,
    File,
    Image,
    NotSet,
    NotSetT,
)

try:
    from prefab_ui.app import PrefabApp as _PrefabApp
    from prefab_ui.components.base import Component as _PrefabComponent

    _HAS_PREFAB = True
except ImportError:
    _HAS_PREFAB = False

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

    from fastmcp.tools.function_tool import FunctionTool
    from fastmcp.tools.tool_transform import ArgTransform, TransformedTool

# Re-export from function_tool module

logger = get_logger(__name__)


def resolve_serialize_by_alias(value: Any) -> bool:
    """Resolve the effective ``by_alias`` setting for serializing *value*.

    Pydantic's low-level serialization helpers (``to_json``,
    ``to_jsonable_python``) default ``by_alias`` to ``True``, which silently
    ignores a model's ``serialize_by_alias`` config. When *value* is a Pydantic
    model we consult that config instead, falling back to ``True`` to preserve
    FastMCP's longstanding default of emitting aliases when no preference is
    declared.
    """
    if isinstance(value, type):
        model = value if issubclass(value, BaseModel) else None
    elif isinstance(value, BaseModel):
        model = type(value)
    else:
        model = None

    if model is None:
        return True

    configured = model.model_config.get("serialize_by_alias")
    return True if configured is None else configured


def default_serializer(data: Any) -> str:
    return pydantic_core.to_json(
        data, fallback=str, by_alias=resolve_serialize_by_alias(data)
    ).decode()


class ToolResult(BaseModel):
    content: list[ContentBlock] = Field(
        description="List of content blocks for the tool result"
    )
    structured_content: dict[str, Any] | None = Field(
        default=None, description="Structured content matching the tool's output schema"
    )
    meta: dict[str, Any] | None = Field(
        default=None, description="Runtime metadata about the tool execution"
    )
    is_error: bool = Field(
        default=False,
        description="Whether this result represents a tool execution error. "
        "When True, it maps to CallToolResult.is_error so the error is returned "
        "to the client rather than raised.",
    )

    def __init__(
        self,
        content: list[ContentBlock] | Any | None = None,
        structured_content: dict[str, Any] | Any | None = None,
        meta: dict[str, Any] | None = None,
        is_error: bool = False,
    ):
        if content is None and structured_content is None:
            raise ValueError("Either content or structured_content must be provided")
        elif content is None:
            content = structured_content

        converted_content: list[ContentBlock] = _convert_to_content(result=content)

        if structured_content is not None:
            # Convert Prefab types to their wire-format envelope before
            # generic serialization, so the renderer gets the right shape.
            if _HAS_PREFAB:
                if isinstance(structured_content, _PrefabApp):
                    structured_content = _prefab_to_json(structured_content)
                elif isinstance(structured_content, _PrefabComponent):
                    structured_content = _prefab_to_json(
                        _PrefabApp(view=structured_content)
                    )

            try:
                structured_content = pydantic_core.to_jsonable_python(
                    value=structured_content,
                    by_alias=resolve_serialize_by_alias(structured_content),
                )
            except pydantic_core.PydanticSerializationError as e:
                logger.error(
                    f"Could not serialize structured content. If this is unexpected, set your tool's output_schema to None to disable automatic serialization: {e}"
                )
                raise
            if not isinstance(structured_content, dict):
                raise ValueError(
                    "structured_content must be a dict or None. "
                    f"Got {type(structured_content).__name__}: {structured_content!r}. "
                    "Tools should wrap non-dict values based on their output_schema."
                )

        super().__init__(
            content=converted_content,
            structured_content=structured_content,
            meta=meta,
            is_error=is_error,
        )

    def to_mcp_result(
        self,
    ) -> (
        list[ContentBlock] | tuple[list[ContentBlock], dict[str, Any]] | CallToolResult
    ):
        # An error result must round-trip through CallToolResult so isError
        # reaches the client; the plain content/tuple returns can't carry it.
        if self.meta is not None or self.is_error:
            return CallToolResult(
                structured_content=self.structured_content,
                content=self.content,
                is_error=self.is_error,
                _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
            )
        if self.structured_content is None:
            return self.content
        return self.content, self.structured_content


class InputRequiredToolResult(ToolResult):
    """The full result of a single multi-round-trip leg (SEP-2322).

    The protocol is stateless: each MRTR leg is a complete request→response
    cycle. When a guard tool returns an `InputRequiredResult` from its body to
    ask the client for input, that ask is the *legitimate result* of this tool
    call — not a pause, not an error, not a third control-flow outcome. FastMCP
    wraps it in this `ToolResult` subclass so it flows through the middleware
    chain as an ordinary return value: `call_next(...)` returns it, default
    middleware completes normally on the leg, and middleware authors can
    identify an ask with a simple `isinstance(result, InputRequiredToolResult)`
    check.

    Invariant: the wrapped `InputRequiredResult` is never serialized as tool
    content. `content` is always empty; the wire handler (`_on_call_tool`)
    reads `.input_required` and returns it to the runner as the
    `input_required` result. Do not read `.content` / `.structured_content` on
    this subclass — they carry nothing.
    """

    input_required: mcp_types.InputRequiredResult = Field(
        description="The client-input request this leg resolved to (SEP-2322)"
    )

    def __init__(self, input_required: mcp_types.InputRequiredResult) -> None:
        # Bypass ToolResult's content-conversion __init__: an input-required
        # leg carries no tool content (see the invariant above), and
        # `input_required` is a required field ToolResult.__init__ can't set.
        BaseModel.__init__(
            self,
            content=[],
            structured_content=None,
            meta=None,
            is_error=False,
            input_required=input_required,
        )


class Tool(FastMCPComponent):
    """Internal tool registration info."""

    KEY_PREFIX: ClassVar[str] = "tool"

    parameters: Annotated[
        dict[str, Any], Field(description="JSON schema for tool parameters")
    ]
    output_schema: Annotated[
        dict[str, Any] | None, Field(description="JSON schema for tool output")
    ] = None
    annotations: Annotated[
        ToolAnnotations | None,
        Field(description="Additional annotations about the tool"),
    ] = None
    execution: Annotated[
        ToolExecution | None,
        Field(description="Task execution configuration (SEP-1686)"),
    ] = None
    auth: Annotated[
        SkipJsonSchema[AuthCheck | list[AuthCheck] | None],
        Field(description="Authorization checks for this tool", exclude=True),
    ] = None
    timeout: Annotated[
        float | None,
        Field(
            description="Execution timeout in seconds. If None, no timeout is applied."
        ),
    ] = None

    @model_validator(mode="after")
    def _validate_tool_name(self) -> Tool:
        """Validate tool name according to MCP specification (SEP-986)."""
        validate_and_warn_tool_name(self.name)
        return self

    def to_mcp_tool(
        self,
        **overrides: Any,
    ) -> MCPTool:
        """Convert the FastMCP tool to an MCP tool."""
        title = None

        if self.title:
            title = self.title
        elif self.annotations and self.annotations.title:
            title = self.annotations.title

        mcp_tool = MCPTool(
            name=overrides.get("name", self.name),
            title=overrides.get("title", title),
            description=overrides.get("description", self.description),
            input_schema=overrides.get("inputSchema", self.parameters),
            output_schema=overrides.get("outputSchema", self.output_schema),
            icons=overrides.get("icons", self.icons),
            annotations=overrides.get("annotations", self.annotations),
            execution=overrides.get("execution", self.execution),
            _meta=overrides.get(  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                "_meta", self.get_meta()
            ),
        )

        if (
            self.task_config.supports_tasks()
            and "execution" not in overrides
            and not self.execution
        ):
            mcp_tool.execution = ToolExecution(task_support=self.task_config.mode)

        return mcp_tool

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        version: str | int | None = None,
        title: str | None = None,
        description: str | None = None,
        icons: list[Icon] | None = None,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        timeout: float | None = None,
        auth: AuthCheck | list[AuthCheck] | None = None,
        run_in_thread: bool | None = None,
    ) -> FunctionTool:
        """Create a Tool from a function."""
        from fastmcp.tools.function_tool import FunctionTool

        return FunctionTool.from_function(
            fn=fn,
            name=name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            annotations=annotations,
            output_schema=output_schema,
            meta=meta,
            task=task,
            timeout=timeout,
            auth=auth,
            run_in_thread=run_in_thread,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """
        Run the tool with arguments.

        This method is not implemented in the base Tool class and must be
        implemented by subclasses.

        `run()` can EITHER return a list of ContentBlocks, or a tuple of
        (list of ContentBlocks, dict of structured output).

        A tool that requests client input (SEP-2322 multi-round-trip) does so by
        returning an `InputRequiredResult` from its body; the run machinery wraps
        that in an `InputRequiredToolResult` — a `ToolResult` subclass — so it
        stays inside the declared `ToolResult` result type and flows through the
        middleware chain as an ordinary result (see `FunctionTool.run`).
        """
        raise NotImplementedError("Subclasses must implement run()")

    def convert_result(self, raw_value: Any) -> ToolResult:
        """Convert a raw result to ToolResult.

        Handles ToolResult passthrough and converts raw values using the tool's
        attributes (output_schema) for proper conversion.
        """
        if isinstance(raw_value, ToolResult):
            return raw_value

        if _HAS_PREFAB:
            if isinstance(raw_value, _PrefabApp):
                return _prefab_to_tool_result(
                    raw_value,
                    fastmcp_app_name=_get_fastmcp_app_name(self),
                )
            if isinstance(raw_value, _PrefabComponent):
                return _prefab_to_tool_result(
                    _PrefabApp(view=raw_value),
                    fastmcp_app_name=_get_fastmcp_app_name(self),
                )

        content = _convert_to_content(raw_value)

        # Bytes can't be represented as structured JSON content
        if isinstance(raw_value, bytes):
            return ToolResult(content=content)

        # Skip structured content for ContentBlock types only if no output_schema
        # (if output_schema exists, MCP SDK requires structured_content)
        if self.output_schema is None and (
            isinstance(raw_value, ContentBlock | Audio | Image | File)
            or (
                isinstance(raw_value, list | tuple)
                and any(isinstance(item, ContentBlock) for item in raw_value)
            )
        ):
            return ToolResult(content=content)

        try:
            structured = pydantic_core.to_jsonable_python(
                raw_value, by_alias=resolve_serialize_by_alias(raw_value)
            )
        except (pydantic_core.PydanticSerializationError, UnicodeDecodeError):
            return ToolResult(content=content)

        if self.output_schema is None:
            # No schema - only use structured_content for dicts
            if isinstance(structured, dict):
                return ToolResult(content=content, structured_content=structured)
            return ToolResult(content=content)

        # Has output_schema - wrap if x-fastmcp-wrap-result is set
        wrap_result = self.output_schema.get("x-fastmcp-wrap-result")
        return ToolResult(
            content=content,
            structured_content={"result": structured} if wrap_result else structured,
            meta={"fastmcp": {"wrap_result": True}} if wrap_result else None,
        )

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
    ) -> mcp_types.CreateTaskResult: ...

    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: TaskMeta | None = None,
    ) -> ToolResult | mcp_types.CreateTaskResult:
        """Server entry point that handles task routing.

        This allows ANY Tool subclass to support background execution by setting
        task_config.mode to "supported" or "required". The server calls this
        method instead of run() directly.

        Args:
            arguments: Tool arguments
            task_meta: If provided, execute as background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return ToolResult.

        Returns:
            ToolResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Subclasses can override this to customize task routing behavior.
        For example, FastMCPProviderTool overrides to delegate to child
        middleware without submitting to Docket.
        """
        from fastmcp.server.tasks.routing import check_background_task

        task_result = await check_background_task(
            component=self,
            task_type="tool",
            arguments=arguments,
            task_meta=task_meta,
        )
        if task_result:
            return task_result

        return await self.run(arguments)

    def register_with_docket(self, docket: Docket) -> None:
        """Register this tool with docket for background execution."""
        if not self.task_config.supports_tasks():
            return
        docket.register(self.run, names=[self.key])

    async def add_to_docket(  # type: ignore[override]
        self,
        docket: Docket,
        arguments: dict[str, Any],
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this tool for background execution via docket.

        Args:
            docket: The Docket instance
            arguments: Tool arguments
            fn_key: Function lookup key in Docket registry (defaults to self.key)
            task_key: Redis storage key for the result
            **kwargs: Additional kwargs passed to docket.add()
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)(arguments)

    @classmethod
    def from_tool(
        cls,
        tool: Tool | Callable[..., Any],
        *,
        name: str | None = None,
        title: str | NotSetT | None = NotSet,
        description: str | NotSetT | None = NotSet,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | NotSetT | None = NotSet,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        meta: dict[str, Any] | NotSetT | None = NotSet,
        transform_args: dict[str, ArgTransform] | None = None,
        transform_fn: Callable[..., Any] | None = None,
    ) -> TransformedTool:
        from fastmcp.tools.tool_transform import TransformedTool

        tool = cls._ensure_tool(tool)

        return TransformedTool.from_tool(
            tool=tool,
            transform_fn=transform_fn,
            name=name,
            title=title,
            transform_args=transform_args,
            description=description,
            tags=tags,
            annotations=annotations,
            output_schema=output_schema,
            meta=meta,
        )

    @classmethod
    def _ensure_tool(cls, tool: Tool | Callable[..., Any]) -> Tool:
        """Coerce a callable into a Tool, respecting @tool decorator metadata."""
        if isinstance(tool, Tool):
            return tool

        from fastmcp.decorators import get_fastmcp_meta
        from fastmcp.tools.function_tool import FunctionTool, ToolMeta

        fmeta = get_fastmcp_meta(tool)
        if isinstance(fmeta, ToolMeta):
            return FunctionTool.from_function(tool, metadata=fmeta)

        return cls.from_function(tool)

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.component.type": "tool",
            "fastmcp.provider.type": "LocalProvider",
        }


def _convert_to_single_content_block(
    item: Any,
) -> ContentBlock:
    if isinstance(item, ContentBlock):
        return item

    if isinstance(item, Image):
        return item.to_image_content()

    if isinstance(item, Audio):
        return item.to_audio_content()

    if isinstance(item, File):
        return item.to_resource_content()

    if isinstance(item, str):
        return TextContent(type="text", text=item)

    if isinstance(item, bytes):
        try:
            return TextContent(type="text", text=item.decode("utf-8"))
        except UnicodeDecodeError:
            import base64

            return TextContent(type="text", text=base64.b64encode(item).decode("ascii"))

    return TextContent(type="text", text=default_serializer(item))


_PREFAB_TEXT_FALLBACK = "[Rendered Prefab UI]"


def _get_tool_resolver(app_name: str | None = None) -> Callable[..., str] | None:
    """Get the Prefab peer-reference resolver bound to an app name."""
    try:
        from fastmcp.apps.app import _make_resolver

        return _make_resolver(app_name)
    except ImportError:
        return None


def _prefab_to_json(app: Any, fastmcp_app_name: str | None = None) -> dict[str, Any]:
    """Call PrefabApp.to_json() with the hash-based resolver.

    The resolver prefixes peer-tool references with a deterministic hash
    derived from the app name + tool name. The dispatcher recognizes that
    format and routes calls via ``get_tool_by_hash`` which walks the
    provider tree recursively — same pattern as the old ``get_app_tool``.
    """
    data = app.to_json(tool_resolver=_get_tool_resolver(fastmcp_app_name))
    return data


def _get_fastmcp_app_name(tool: Tool) -> str | None:
    """Read the FastMCPApp name from a tool's metadata, if present."""
    meta = tool.meta
    if not meta:
        return None
    fastmcp_meta = meta.get("fastmcp")
    if isinstance(fastmcp_meta, dict):
        app = fastmcp_meta.get("app")
        if isinstance(app, str):
            return app
    return None


def _prefab_to_tool_result(app: Any, fastmcp_app_name: str | None = None) -> ToolResult:
    """Convert a PrefabApp to a FastMCP ToolResult."""
    return ToolResult(
        content=[TextContent(type="text", text=_PREFAB_TEXT_FALLBACK)],
        structured_content=_prefab_to_json(app, fastmcp_app_name=fastmcp_app_name),
    )


def _convert_to_content(
    result: Any,
) -> list[ContentBlock]:
    """Convert a result to a sequence of content objects."""

    if result is None:
        return []

    if not isinstance(result, (list | tuple)):
        return [_convert_to_single_content_block(result)]

    # If all items are ContentBlocks, return them as is
    if all(isinstance(item, ContentBlock) for item in result):
        return result

    # If any item is a ContentBlock, convert non-ContentBlock items to TextContent
    # without aggregating them
    if any(isinstance(item, ContentBlock | Image | Audio | File) for item in result):
        return [
            _convert_to_single_content_block(item)
            if not isinstance(item, ContentBlock)
            else item
            for item in result
        ]
    # If none of the items are ContentBlocks, aggregate all items into a single TextContent
    return [TextContent(type="text", text=default_serializer(result))]


__all__ = ["InputRequiredToolResult", "Tool", "ToolResult"]
