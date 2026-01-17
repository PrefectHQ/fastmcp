from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    TypeAlias,
    overload,
)

import mcp.types
import pydantic_core
from mcp.shared.tool_name_validation import validate_and_warn_tool_name
from mcp.types import (
    CallToolResult,
    ContentBlock,
    Icon,
    TextContent,
    ToolAnnotations,
    ToolExecution,
)
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, Field, model_validator

from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    Audio,
    File,
    Image,
    NotSet,
    NotSetT,
)

# Runtime type alias for auth checks to avoid circular imports with authorization.py
# AuthCheck is Callable[[AuthContext], bool] but we use Any to avoid the import
AuthCheckCallable: TypeAlias = Callable[[Any], bool]

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

    from fastmcp.tools.function_tool import FunctionTool
    from fastmcp.tools.tool_transform import ArgTransform, TransformedTool

# Re-export from function_tool module

logger = get_logger(__name__)


ToolResultSerializerType: TypeAlias = Callable[[Any], str]


def default_serializer(data: Any) -> str:
    return pydantic_core.to_json(data, fallback=str).decode()


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

    def __init__(
        self,
        content: list[ContentBlock] | Any | None = None,
        structured_content: dict[str, Any] | Any | None = None,
        meta: dict[str, Any] | None = None,
    ):
        if content is None and structured_content is None:
            raise ValueError("Either content or structured_content must be provided")
        elif content is None:
            content = structured_content

        converted_content: list[ContentBlock] = _convert_to_content(result=content)

        if structured_content is not None:
            try:
                structured_content = pydantic_core.to_jsonable_python(
                    value=structured_content
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
            content=converted_content, structured_content=structured_content, meta=meta
        )

    def to_mcp_result(
        self,
    ) -> (
        list[ContentBlock] | tuple[list[ContentBlock], dict[str, Any]] | CallToolResult
    ):
        if self.meta is not None:
            return CallToolResult(
                structuredContent=self.structured_content,
                content=self.content,
                _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
            )
        if self.structured_content is None:
            return self.content
        return self.content, self.structured_content


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
    serializer: Annotated[
        ToolResultSerializerType | None,
        Field(
            description="Deprecated. Return ToolResult from your tools for full control over serialization."
        ),
    ] = None
    auth: Annotated[
        AuthCheckCallable | list[AuthCheckCallable] | None,
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

        return MCPTool(
            name=overrides.get("name", self.name),
            title=overrides.get("title", title),
            description=overrides.get("description", self.description),
            inputSchema=overrides.get("inputSchema", self.parameters),
            outputSchema=overrides.get("outputSchema", self.output_schema),
            icons=overrides.get("icons", self.icons),
            annotations=overrides.get("annotations", self.annotations),
            execution=overrides.get("execution", self.execution),
            _meta=overrides.get(  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
                "_meta", self.get_meta()
            ),
        )

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
        exclude_args: list[str] | None = None,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,  # Deprecated
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        timeout: float | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
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
            exclude_args=exclude_args,
            output_schema=output_schema,
            serializer=serializer,
            meta=meta,
            task=task,
            timeout=timeout,
            auth=auth,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """
        Run the tool with arguments.

        This method is not implemented in the base Tool class and must be
        implemented by subclasses.

        `run()` can EITHER return a list of ContentBlocks, or a tuple of
        (list of ContentBlocks, dict of structured output).
        """
        raise NotImplementedError("Subclasses must implement run()")

    def convert_result(self, raw_value: Any) -> ToolResult:
        """Convert a raw result to ToolResult.

        Handles ToolResult passthrough and converts raw values using the tool's
        attributes (serializer, output_schema) for proper conversion.
        """
        if isinstance(raw_value, ToolResult):
            return raw_value

        content = _convert_to_content(raw_value, serializer=self.serializer)

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
            structured = pydantic_core.to_jsonable_python(raw_value)
        except pydantic_core.PydanticSerializationError:
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
    ) -> mcp.types.CreateTaskResult: ...

    async def _run(
        self,
        arguments: dict[str, Any],
        task_meta: TaskMeta | None = None,
    ) -> ToolResult | mcp.types.CreateTaskResult:
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
        tool: Tool,
        *,
        name: str | None = None,
        title: str | NotSetT | None = NotSet,
        description: str | NotSetT | None = NotSet,
        tags: set[str] | None = None,
        annotations: ToolAnnotations | NotSetT | None = NotSet,
        output_schema: dict[str, Any] | NotSetT | None = NotSet,
        serializer: ToolResultSerializerType | None = None,  # Deprecated
        meta: dict[str, Any] | NotSetT | None = NotSet,
        transform_args: dict[str, ArgTransform] | None = None,
        transform_fn: Callable[..., Any] | None = None,
    ) -> TransformedTool:
        from fastmcp.tools.tool_transform import TransformedTool

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
            serializer=serializer,
            meta=meta,
        )

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.component.type": "tool",
            "fastmcp.provider.type": "LocalProvider",
        }


def _serialize_with_fallback(
    result: Any, serializer: ToolResultSerializerType | None = None
) -> str:
    if serializer is not None:
        try:
            return serializer(result)
        except Exception as e:
            logger.warning(
                "Error serializing tool result: %s",
                e,
                exc_info=True,
            )

    return default_serializer(result)


def _convert_to_single_content_block(
    item: Any,
    serializer: ToolResultSerializerType | None = None,
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

    return TextContent(type="text", text=_serialize_with_fallback(item, serializer))


def _convert_to_content(
    result: Any,
    serializer: ToolResultSerializerType | None = None,
) -> list[ContentBlock]:
    """Convert a result to a sequence of content objects."""

    if result is None:
        return []

    if not isinstance(result, (list | tuple)):
        return [_convert_to_single_content_block(result, serializer)]

    # If all items are ContentBlocks, return them as is
    if all(isinstance(item, ContentBlock) for item in result):
        return result

    # If any item is a ContentBlock, convert non-ContentBlock items to TextContent
    # without aggregating them
    if any(isinstance(item, ContentBlock | Image | Audio | File) for item in result):
        return [
            _convert_to_single_content_block(item, serializer)
            if not isinstance(item, ContentBlock)
            else item
            for item in result
        ]
    # If none of the items are ContentBlocks, aggregate all items into a single TextContent
    return [TextContent(type="text", text=_serialize_with_fallback(result, serializer))]


__all__ = ["Tool", "ToolResult"]


def __getattr__(name: str) -> Any:
    """Deprecated re-exports for backwards compatibility."""
    deprecated_exports = {
        "FunctionTool": "FunctionTool",
        "ParsedFunction": "ParsedFunction",
        "tool": "tool",
    }

    if name in deprecated_exports:
        import fastmcp

        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                f"Importing {name} from fastmcp.tools.tool is deprecated. "
                f"Import from fastmcp.tools.function_tool instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        from fastmcp.tools import function_tool

        return getattr(function_tool, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
