"""Anthropic sampling handler for FastMCP."""

from collections.abc import Iterator, Sequence
from typing import Any

from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import (
    CreateMessageResult,
    CreateMessageResultWithTools,
    ModelPreferences,
    SamplingMessage,
    SamplingMessageContentBlock,
    StopReason,
    TextContent,
    Tool,
    ToolChoice,
    ToolResultContent,
    ToolUseContent,
)

try:
    from anthropic import AsyncAnthropic, NotGiven
    from anthropic._types import NOT_GIVEN
    from anthropic.types import (
        Message,
        MessageParam,
        TextBlock,
        TextBlockParam,
        ToolParam,
        ToolResultBlockParam,
        ToolUseBlock,
        ToolUseBlockParam,
    )
    from anthropic.types.model_param import ModelParam
    from anthropic.types.tool_choice_any_param import ToolChoiceAnyParam
    from anthropic.types.tool_choice_auto_param import ToolChoiceAutoParam
    from anthropic.types.tool_choice_param import ToolChoiceParam
except ImportError as e:
    raise ImportError(
        "The `anthropic` package is not installed. "
        "Install it with `pip install fastmcp[anthropic]` or add `anthropic` to your dependencies."
    ) from e

__all__ = ["AnthropicSamplingHandler"]


class AnthropicSamplingHandler:
    """Sampling handler that uses the Anthropic API.

    Example:
        ```python
        from anthropic import AsyncAnthropic
        from fastmcp import FastMCP
        from fastmcp.client.sampling.handlers.anthropic import AnthropicSamplingHandler

        handler = AnthropicSamplingHandler(
            default_model="claude-sonnet-4-5",
            client=AsyncAnthropic(),
        )

        server = FastMCP(sampling_handler=handler)
        ```
    """

    def __init__(
        self, default_model: ModelParam, client: AsyncAnthropic | None = None
    ) -> None:
        self.client: AsyncAnthropic = client or AsyncAnthropic()
        self.default_model: ModelParam = default_model

    async def __call__(
        self,
        messages: list[SamplingMessage],
        params: SamplingParams,
        context: Any,
    ) -> CreateMessageResult | CreateMessageResultWithTools:
        anthropic_messages: list[MessageParam] = self._convert_to_anthropic_messages(
            messages=messages,
        )

        model: ModelParam = self._select_model_from_preferences(params.modelPreferences)

        # Convert MCP tools to Anthropic format
        anthropic_tools: list[ToolParam] | NotGiven = NOT_GIVEN
        if params.tools:
            anthropic_tools = self._convert_tools_to_anthropic(params.tools)

        # Convert tool_choice to Anthropic format
        # Returns None if mode is "none", signaling tools should be omitted
        anthropic_tool_choice: ToolChoiceParam | NotGiven = NOT_GIVEN
        if params.toolChoice:
            converted = self._convert_tool_choice_to_anthropic(params.toolChoice)
            if converted is None:
                # tool_choice="none" means don't use tools
                anthropic_tools = NOT_GIVEN
            else:
                anthropic_tool_choice = converted

        response = await self.client.messages.create(
            model=model,
            messages=anthropic_messages,
            system=(
                params.systemPrompt if params.systemPrompt is not None else NOT_GIVEN
            ),
            temperature=(
                params.temperature if params.temperature is not None else NOT_GIVEN
            ),
            max_tokens=params.maxTokens,
            stop_sequences=(
                params.stopSequences if params.stopSequences is not None else NOT_GIVEN
            ),
            tools=anthropic_tools,
            tool_choice=anthropic_tool_choice,
        )

        # Return appropriate result type based on whether tools were provided
        if params.tools:
            return self._message_to_result_with_tools(response)
        return self._message_to_create_message_result(response)

    @staticmethod
    def _iter_models_from_preferences(
        model_preferences: ModelPreferences | str | list[str] | None,
    ) -> Iterator[str]:
        if model_preferences is None:
            return

        if isinstance(model_preferences, str):
            yield model_preferences

        elif isinstance(model_preferences, list):
            yield from model_preferences

        elif isinstance(model_preferences, ModelPreferences):
            if not (hints := model_preferences.hints):
                return

            for hint in hints:
                if not (name := hint.name):
                    continue

                yield name

    @staticmethod
    def _convert_to_anthropic_messages(
        messages: Sequence[SamplingMessage],
    ) -> list[MessageParam]:
        anthropic_messages: list[MessageParam] = []

        for message in messages:
            content = message.content

            # Handle list content (from CreateMessageResultWithTools)
            if isinstance(content, list):
                content_blocks: list[
                    TextBlockParam | ToolUseBlockParam | ToolResultBlockParam
                ] = []

                for item in content:
                    if isinstance(item, ToolUseContent):
                        content_blocks.append(
                            ToolUseBlockParam(
                                type="tool_use",
                                id=item.id,
                                name=item.name,
                                input=item.input,
                            )
                        )
                    elif isinstance(item, TextContent):
                        content_blocks.append(
                            TextBlockParam(type="text", text=item.text)
                        )
                    elif isinstance(item, ToolResultContent):
                        # Extract text content from the result
                        result_content: str | list[TextBlockParam] = ""
                        if item.content:
                            text_blocks: list[TextBlockParam] = []
                            for sub_item in item.content:
                                if isinstance(sub_item, TextContent):
                                    text_blocks.append(
                                        TextBlockParam(type="text", text=sub_item.text)
                                    )
                            if len(text_blocks) == 1:
                                result_content = text_blocks[0]["text"]
                            elif text_blocks:
                                result_content = text_blocks

                        content_blocks.append(
                            ToolResultBlockParam(
                                type="tool_result",
                                tool_use_id=item.toolUseId,
                                content=result_content,
                                is_error=item.isError if item.isError else False,
                            )
                        )

                if content_blocks:
                    anthropic_messages.append(
                        MessageParam(
                            role=message.role,
                            content=content_blocks,
                        )
                    )
                continue

            # Handle ToolUseContent (assistant's tool calls)
            if isinstance(content, ToolUseContent):
                anthropic_messages.append(
                    MessageParam(
                        role="assistant",
                        content=[
                            ToolUseBlockParam(
                                type="tool_use",
                                id=content.id,
                                name=content.name,
                                input=content.input,
                            )
                        ],
                    )
                )
                continue

            # Handle ToolResultContent (user's tool results)
            if isinstance(content, ToolResultContent):
                result_content_str: str | list[TextBlockParam] = ""
                if content.content:
                    text_parts: list[TextBlockParam] = []
                    for item in content.content:
                        if isinstance(item, TextContent):
                            text_parts.append(
                                TextBlockParam(type="text", text=item.text)
                            )
                    if len(text_parts) == 1:
                        result_content_str = text_parts[0]["text"]
                    elif text_parts:
                        result_content_str = text_parts

                anthropic_messages.append(
                    MessageParam(
                        role="user",
                        content=[
                            ToolResultBlockParam(
                                type="tool_result",
                                tool_use_id=content.toolUseId,
                                content=result_content_str,
                                is_error=content.isError if content.isError else False,
                            )
                        ],
                    )
                )
                continue

            # Handle TextContent
            if isinstance(content, TextContent):
                anthropic_messages.append(
                    MessageParam(
                        role=message.role,
                        content=content.text,
                    )
                )
                continue

            raise ValueError(f"Unsupported content type: {type(content)}")

        return anthropic_messages

    @staticmethod
    def _message_to_create_message_result(
        message: Message,
    ) -> CreateMessageResult:
        if len(message.content) == 0:
            raise ValueError("No content in response from Anthropic")

        # Join all text blocks to avoid dropping content
        text = "".join(
            block.text for block in message.content if isinstance(block, TextBlock)
        )
        if text:
            return CreateMessageResult(
                content=TextContent(type="text", text=text),
                role="assistant",
                model=message.model,
            )

        raise ValueError(
            f"No text content in response from Anthropic: {[type(b).__name__ for b in message.content]}"
        )

    def _select_model_from_preferences(
        self, model_preferences: ModelPreferences | str | list[str] | None
    ) -> ModelParam:
        for model_option in self._iter_models_from_preferences(model_preferences):
            # Accept any model that starts with "claude"
            if model_option.startswith("claude"):
                return model_option

        return self.default_model

    @staticmethod
    def _convert_tools_to_anthropic(tools: list[Tool]) -> list[ToolParam]:
        """Convert MCP tools to Anthropic tool format."""
        anthropic_tools: list[ToolParam] = []
        for tool in tools:
            # Build input_schema dict, ensuring required fields
            input_schema: dict[str, Any] = dict(tool.inputSchema)
            if "type" not in input_schema:
                input_schema["type"] = "object"

            anthropic_tools.append(
                ToolParam(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=input_schema,
                )
            )
        return anthropic_tools

    @staticmethod
    def _convert_tool_choice_to_anthropic(
        tool_choice: ToolChoice,
    ) -> ToolChoiceParam | None:
        """Convert MCP tool_choice to Anthropic format.

        Returns None for "none" mode, signaling that tools should be omitted
        from the request entirely (Anthropic doesn't have an explicit "none" option).
        """
        if tool_choice.mode == "auto":
            return ToolChoiceAutoParam(type="auto")
        elif tool_choice.mode == "required":
            return ToolChoiceAnyParam(type="any")
        elif tool_choice.mode == "none":
            # Anthropic doesn't have a "none" option - return None to signal
            # that tools should be omitted from the request entirely
            return None
        else:
            raise ValueError(f"Unsupported tool_choice mode: {tool_choice.mode!r}")

    @staticmethod
    def _message_to_result_with_tools(
        message: Message,
    ) -> CreateMessageResultWithTools:
        """Convert Anthropic response to CreateMessageResultWithTools."""
        if len(message.content) == 0:
            raise ValueError("No content in response from Anthropic")

        # Determine stop reason
        stop_reason: StopReason
        if message.stop_reason == "tool_use":
            stop_reason = "toolUse"
        elif message.stop_reason == "end_turn":
            stop_reason = "endTurn"
        elif message.stop_reason == "max_tokens":
            stop_reason = "maxTokens"
        elif message.stop_reason == "stop_sequence":
            stop_reason = "endTurn"
        else:
            stop_reason = "endTurn"

        # Build content list
        content: list[SamplingMessageContentBlock] = []

        for block in message.content:
            if isinstance(block, TextBlock):
                content.append(TextContent(type="text", text=block.text))
            elif isinstance(block, ToolUseBlock):
                # Anthropic returns input as dict directly
                arguments = block.input if isinstance(block.input, dict) else {}

                content.append(
                    ToolUseContent(
                        type="tool_use",
                        id=block.id,
                        name=block.name,
                        input=arguments,
                    )
                )

        # Must have at least some content
        if not content:
            raise ValueError("No content in response from Anthropic")

        return CreateMessageResultWithTools(
            content=content,
            role="assistant",
            model=message.model,
            stopReason=stop_reason,
        )
