"""Base classes for FastMCP prompts."""

from __future__ import annotations as _annotations

import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar, Literal, overload

import pydantic
import pydantic_core

if TYPE_CHECKING:
    from docket import Docket
    from docket.execution import Execution

    from fastmcp.prompts.function_prompt import FunctionPrompt
import mcp.types
from mcp import GetPromptResult
from mcp.types import (
    EmbeddedResource,
    Icon,
    PromptMessage,
    TextContent,
)
from mcp.types import Prompt as SDKPrompt
from mcp.types import PromptArgument as SDKPromptArgument
from pydantic import Field

from fastmcp.server.tasks.config import TaskConfig, TaskMeta
from fastmcp.tools.tool import AuthCheckCallable
from fastmcp.utilities.components import FastMCPComponent
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import (
    FastMCPBaseModel,
)

logger = get_logger(__name__)


class Message(pydantic.BaseModel):
    """Wrapper for prompt message with auto-serialization.

    Accepts any content - strings pass through, other types
    (dict, list, BaseModel) are JSON-serialized to text.

    Example:
        ```python
        from fastmcp.prompts import Message

        # String content (user role by default)
        Message("Hello, world!")

        # Explicit role
        Message("I can help with that.", role="assistant")

        # Auto-serialized to JSON
        Message({"key": "value"})
        Message(["item1", "item2"])
        ```
    """

    role: Literal["user", "assistant"]
    content: TextContent | EmbeddedResource

    def __init__(
        self,
        content: Any,
        role: Literal["user", "assistant"] = "user",
    ):
        """Create Message with automatic serialization.

        Args:
            content: The message content. str passes through directly.
                     TextContent and EmbeddedResource pass through.
                     Other types (dict, list, BaseModel) are JSON-serialized.
            role: The message role, either "user" or "assistant".
        """
        # Handle already-wrapped content types
        if isinstance(content, (TextContent, EmbeddedResource)):
            normalized_content: TextContent | EmbeddedResource = content
        elif isinstance(content, str):
            normalized_content = TextContent(type="text", text=content)
        else:
            # dict, list, BaseModel → JSON string
            serialized = pydantic_core.to_json(content, fallback=str).decode()
            normalized_content = TextContent(type="text", text=serialized)

        super().__init__(role=role, content=normalized_content)

    def to_mcp_prompt_message(self) -> PromptMessage:
        """Convert to MCP PromptMessage."""
        return PromptMessage(role=self.role, content=self.content)


class PromptArgument(FastMCPBaseModel):
    """An argument that can be passed to a prompt."""

    name: str = Field(description="Name of the argument")
    description: str | None = Field(
        default=None, description="Description of what the argument does"
    )
    required: bool = Field(
        default=False, description="Whether the argument is required"
    )


class PromptResult(pydantic.BaseModel):
    """Canonical result type for prompt rendering.

    Provides explicit control over prompt responses: multiple messages,
    roles, and metadata at both the message and result level.

    Accepts:
        - str: Wrapped as single Message (user role)
        - list[Message]: Used directly for multiple messages or custom roles

    Example:
        ```python
        from fastmcp import FastMCP
        from fastmcp.prompts import PromptResult, Message

        mcp = FastMCP()

        # Simple string content
        @mcp.prompt
        def greet() -> PromptResult:
            return PromptResult("Hello!")

        # Multiple messages with roles
        @mcp.prompt
        def conversation() -> PromptResult:
            return PromptResult([
                Message("What's the weather?"),
                Message("It's sunny today.", role="assistant"),
            ])
        ```
    """

    messages: list[Message]
    description: str | None = None
    meta: dict[str, Any] | None = None

    def __init__(
        self,
        messages: str | list[Message],
        description: str | None = None,
        meta: dict[str, Any] | None = None,
    ):
        """Create PromptResult.

        Args:
            messages: String or list of Message objects.
            description: Optional description of the prompt result.
            meta: Optional metadata about the prompt result.
        """
        normalized = self._normalize_messages(messages)
        super().__init__(messages=normalized, description=description, meta=meta)

    @staticmethod
    def _normalize_messages(
        messages: str | list[Message],
    ) -> list[Message]:
        """Normalize input to list[Message]."""
        if isinstance(messages, str):
            return [Message(messages)]
        if isinstance(messages, list):
            # Validate all items are Message
            for i, item in enumerate(messages):
                if not isinstance(item, Message):
                    raise TypeError(
                        f"messages[{i}] must be Message, got {type(item).__name__}. "
                        f"Use Message({item!r}) to wrap the value."
                    )
            return messages
        raise TypeError(
            f"messages must be str or list[Message], got {type(messages).__name__}"
        )

    def to_mcp_prompt_result(self) -> GetPromptResult:
        """Convert to MCP GetPromptResult."""
        mcp_messages = [m.to_mcp_prompt_message() for m in self.messages]
        return GetPromptResult(
            description=self.description,
            messages=mcp_messages,
            _meta=self.meta,  # type: ignore[call-arg]  # _meta is Pydantic alias for meta field
        )


class Prompt(FastMCPComponent):
    """A prompt template that can be rendered with parameters."""

    KEY_PREFIX: ClassVar[str] = "prompt"

    arguments: list[PromptArgument] | None = Field(
        default=None, description="Arguments that can be passed to the prompt"
    )
    auth: AuthCheckCallable | list[AuthCheckCallable] | None = Field(
        default=None, description="Authorization checks for this prompt", exclude=True
    )

    def to_mcp_prompt(
        self,
        **overrides: Any,
    ) -> SDKPrompt:
        """Convert the prompt to an MCP prompt."""
        arguments = [
            SDKPromptArgument(
                name=arg.name,
                description=arg.description,
                required=arg.required,
            )
            for arg in self.arguments or []
        ]

        return SDKPrompt(
            name=overrides.get("name", self.name),
            description=overrides.get("description", self.description),
            arguments=arguments,
            title=overrides.get("title", self.title),
            icons=overrides.get("icons", self.icons),
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
        meta: dict[str, Any] | None = None,
        task: bool | TaskConfig | None = None,
        auth: AuthCheckCallable | list[AuthCheckCallable] | None = None,
    ) -> FunctionPrompt:
        """Create a Prompt from a function.

        The function can return:
        - str: wrapped as single user Message
        - list[Message | str]: converted to list[Message]
        - PromptResult: used directly
        """
        from fastmcp.prompts.function_prompt import FunctionPrompt

        return FunctionPrompt.from_function(
            fn=fn,
            name=name,
            version=version,
            title=title,
            description=description,
            icons=icons,
            tags=tags,
            meta=meta,
            task=task,
            auth=auth,
        )

    async def render(
        self,
        arguments: dict[str, Any] | None = None,
    ) -> str | list[Message | str] | PromptResult:
        """Render the prompt with arguments.

        Subclasses must implement this method. Return one of:
        - str: Wrapped as single user Message
        - list[Message | str]: Converted to list[Message]
        - PromptResult: Used directly
        """
        raise NotImplementedError("Subclasses must implement render()")

    def convert_result(self, raw_value: Any) -> PromptResult:
        """Convert a raw return value to PromptResult.

        Accepts:
            - PromptResult: passed through
            - str: wrapped as single Message
            - list[Message | str]: converted to list[Message]

        Raises:
            TypeError: for unsupported types
        """
        if isinstance(raw_value, PromptResult):
            return raw_value

        if isinstance(raw_value, str):
            return PromptResult(raw_value, description=self.description, meta=self.meta)

        if isinstance(raw_value, list | tuple):
            messages: list[Message] = []
            for i, item in enumerate(raw_value):
                if isinstance(item, Message):
                    messages.append(item)
                elif isinstance(item, str):
                    messages.append(Message(item))
                else:
                    raise TypeError(
                        f"messages[{i}] must be Message or str, got {type(item).__name__}. "
                        f"Use Message({item!r}) to wrap the value."
                    )
            return PromptResult(messages, description=self.description, meta=self.meta)

        raise TypeError(
            f"Prompt must return str, list[Message], or PromptResult, "
            f"got {type(raw_value).__name__}"
        )

    @overload
    async def _render(
        self,
        arguments: dict[str, Any] | None = None,
        task_meta: None = None,
    ) -> PromptResult: ...

    @overload
    async def _render(
        self,
        arguments: dict[str, Any] | None,
        task_meta: TaskMeta,
    ) -> mcp.types.CreateTaskResult: ...

    async def _render(
        self,
        arguments: dict[str, Any] | None = None,
        task_meta: TaskMeta | None = None,
    ) -> PromptResult | mcp.types.CreateTaskResult:
        """Server entry point that handles task routing.

        This allows ANY Prompt subclass to support background execution by setting
        task_config.mode to "supported" or "required". The server calls this
        method instead of render() directly.

        Args:
            arguments: Prompt arguments
            task_meta: If provided, execute as background task and return
                CreateTaskResult. If None (default), execute synchronously and
                return PromptResult.

        Returns:
            PromptResult when task_meta is None.
            CreateTaskResult when task_meta is provided.

        Subclasses can override this to customize task routing behavior.
        For example, FastMCPProviderPrompt overrides to delegate to child
        middleware without submitting to Docket.
        """
        from fastmcp.server.tasks.routing import check_background_task

        task_result = await check_background_task(
            component=self,
            task_type="prompt",
            arguments=arguments,
            task_meta=task_meta,
        )
        if task_result:
            return task_result

        # Synchronous execution
        result = await self.render(arguments)
        return self.convert_result(result)

    def register_with_docket(self, docket: Docket) -> None:
        """Register this prompt with docket for background execution."""
        if not self.task_config.supports_tasks():
            return
        docket.register(self.render, names=[self.key])

    async def add_to_docket(  # type: ignore[override]
        self,
        docket: Docket,
        arguments: dict[str, Any] | None,
        *,
        fn_key: str | None = None,
        task_key: str | None = None,
        **kwargs: Any,
    ) -> Execution:
        """Schedule this prompt for background execution via docket.

        Args:
            docket: The Docket instance
            arguments: Prompt arguments
            fn_key: Function lookup key in Docket registry (defaults to self.key)
            task_key: Redis storage key for the result
            **kwargs: Additional kwargs passed to docket.add()
        """
        lookup_key = fn_key or self.key
        if task_key:
            kwargs["key"] = task_key
        return await docket.add(lookup_key, **kwargs)(arguments)

    def get_span_attributes(self) -> dict[str, Any]:
        return super().get_span_attributes() | {
            "fastmcp.component.type": "prompt",
            "fastmcp.provider.type": "LocalProvider",
        }


__all__ = [
    "Message",
    "Prompt",
    "PromptArgument",
    "PromptResult",
]


def __getattr__(name: str) -> Any:
    """Deprecated re-exports for backwards compatibility."""
    deprecated_exports = {
        "FunctionPrompt": "FunctionPrompt",
        "prompt": "prompt",
    }

    if name in deprecated_exports:
        import fastmcp

        if fastmcp.settings.deprecation_warnings:
            warnings.warn(
                f"Importing {name} from fastmcp.prompts.prompt is deprecated. "
                f"Import from fastmcp.prompts.function_prompt instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        from fastmcp.prompts import function_prompt

        return getattr(function_prompt, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
