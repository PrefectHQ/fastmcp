"""OpenAI sampling handler for FastMCP."""

import json
from collections.abc import Iterator, Sequence
from typing import Any, get_args

from mcp import ClientSession, ServerSession
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import (
    CreateMessageResult,
    CreateMessageResultWithTools,
    ModelPreferences,
    SamplingMessage,
    StopReason,
    TextContent,
    Tool,
    ToolChoice,
    ToolResultContent,
    ToolUseContent,
)

try:
    from openai import NOT_GIVEN, AsyncOpenAI, NotGiven
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionAssistantMessageParam,
        ChatCompletionMessageParam,
        ChatCompletionMessageToolCallParam,
        ChatCompletionSystemMessageParam,
        ChatCompletionToolChoiceOptionParam,
        ChatCompletionToolMessageParam,
        ChatCompletionToolParam,
        ChatCompletionUserMessageParam,
    )
    from openai.types.shared.chat_model import ChatModel
    from openai.types.shared_params import FunctionDefinition
except ImportError as e:
    raise ImportError(
        "The `openai` package is not installed. "
        "Please install `fastmcp[openai]` or add `openai` to your dependencies manually."
    ) from e


class OpenAISamplingHandler:
    """Sampling handler that uses the OpenAI API."""

    def __init__(
        self,
        default_model: ChatModel,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.client: AsyncOpenAI = client or AsyncOpenAI()
        self.default_model: ChatModel = default_model

    async def __call__(
        self,
        messages: list[SamplingMessage],
        params: SamplingParams,
        context: RequestContext[ServerSession, LifespanContextT]
        | RequestContext[ClientSession, LifespanContextT],
    ) -> CreateMessageResult | CreateMessageResultWithTools:
        openai_messages: list[ChatCompletionMessageParam] = (
            self._convert_to_openai_messages(
                system_prompt=params.systemPrompt,
                messages=messages,
            )
        )

        model: ChatModel = self._select_model_from_preferences(params.modelPreferences)

        # Convert MCP tools to OpenAI format
        openai_tools: list[ChatCompletionToolParam] | NotGiven = NOT_GIVEN
        if params.tools:
            openai_tools = self._convert_tools_to_openai(params.tools)

        # Convert tool_choice to OpenAI format
        openai_tool_choice: ChatCompletionToolChoiceOptionParam | NotGiven = NOT_GIVEN
        if params.toolChoice:
            openai_tool_choice = self._convert_tool_choice_to_openai(params.toolChoice)

        response = await self.client.chat.completions.create(
            model=model,
            messages=openai_messages,
            temperature=(
                params.temperature if params.temperature is not None else NOT_GIVEN
            ),
            max_tokens=params.maxTokens,
            stop=params.stopSequences if params.stopSequences else NOT_GIVEN,
            tools=openai_tools,
            tool_choice=openai_tool_choice,
        )

        # Return appropriate result type based on whether tools were provided
        if params.tools:
            return self._chat_completion_to_result_with_tools(response)
        return self._chat_completion_to_create_message_result(response)

    @staticmethod
    def _iter_models_from_preferences(
        model_preferences: ModelPreferences | str | list[str] | None,
    ) -> Iterator[str]:
        if model_preferences is None:
            return

        if isinstance(model_preferences, str) and model_preferences in get_args(
            ChatModel
        ):
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
    def _convert_to_openai_messages(
        system_prompt: str | None, messages: Sequence[SamplingMessage]
    ) -> list[ChatCompletionMessageParam]:
        openai_messages: list[ChatCompletionMessageParam] = []

        if system_prompt:
            openai_messages.append(
                ChatCompletionSystemMessageParam(
                    role="system",
                    content=system_prompt,
                )
            )

        for message in messages:
            content = message.content

            # Handle list content (from CreateMessageResultWithTools)
            if isinstance(content, list):
                # Collect tool calls and text from the list
                tool_calls: list[ChatCompletionMessageToolCallParam] = []
                text_parts: list[str] = []
                # Collect tool results separately to maintain correct ordering
                tool_messages: list[ChatCompletionToolMessageParam] = []

                for item in content:
                    if isinstance(item, ToolUseContent):
                        tool_calls.append(
                            ChatCompletionMessageToolCallParam(
                                id=item.id,
                                type="function",
                                function={
                                    "name": item.name,
                                    "arguments": json.dumps(item.input),
                                },
                            )
                        )
                    elif isinstance(item, TextContent):
                        text_parts.append(item.text)
                    elif isinstance(item, ToolResultContent):
                        # Collect tool results (added after assistant message)
                        content_text = ""
                        if item.content:
                            result_texts = []
                            for sub_item in item.content:
                                if isinstance(sub_item, TextContent):
                                    result_texts.append(sub_item.text)
                            content_text = "\n".join(result_texts)
                        tool_messages.append(
                            ChatCompletionToolMessageParam(
                                role="tool",
                                tool_call_id=item.toolUseId,
                                content=content_text,
                            )
                        )

                # Add assistant message with tool calls if present
                # OpenAI requires: assistant (with tool_calls) -> tool messages
                if tool_calls or text_parts:
                    msg_content = "\n".join(text_parts) if text_parts else None
                    if tool_calls:
                        openai_messages.append(
                            ChatCompletionAssistantMessageParam(
                                role="assistant",
                                content=msg_content,
                                tool_calls=tool_calls,
                            )
                        )
                        # Add tool messages AFTER assistant message
                        openai_messages.extend(tool_messages)
                    elif msg_content:
                        if message.role == "user":
                            openai_messages.append(
                                ChatCompletionUserMessageParam(
                                    role="user",
                                    content=msg_content,
                                )
                            )
                        else:
                            openai_messages.append(
                                ChatCompletionAssistantMessageParam(
                                    role="assistant",
                                    content=msg_content,
                                )
                            )
                elif tool_messages:
                    # Tool results only (assistant message was in previous message)
                    openai_messages.extend(tool_messages)
                continue

            # Handle ToolUseContent (assistant's tool calls)
            if isinstance(content, ToolUseContent):
                openai_messages.append(
                    ChatCompletionAssistantMessageParam(
                        role="assistant",
                        tool_calls=[
                            ChatCompletionMessageToolCallParam(
                                id=content.id,
                                type="function",
                                function={
                                    "name": content.name,
                                    "arguments": json.dumps(content.input),
                                },
                            )
                        ],
                    )
                )
                continue

            # Handle ToolResultContent (user's tool results)
            if isinstance(content, ToolResultContent):
                # Extract text parts from the content list
                result_texts: list[str] = []
                if content.content:
                    for item in content.content:
                        if isinstance(item, TextContent):
                            result_texts.append(item.text)
                openai_messages.append(
                    ChatCompletionToolMessageParam(
                        role="tool",
                        tool_call_id=content.toolUseId,
                        content="\n".join(result_texts),
                    )
                )
                continue

            # Handle TextContent
            if isinstance(content, TextContent):
                if message.role == "user":
                    openai_messages.append(
                        ChatCompletionUserMessageParam(
                            role="user",
                            content=content.text,
                        )
                    )
                else:
                    openai_messages.append(
                        ChatCompletionAssistantMessageParam(
                            role="assistant",
                            content=content.text,
                        )
                    )
                continue

            raise ValueError(f"Unsupported content type: {type(content)}")

        return openai_messages

    @staticmethod
    def _chat_completion_to_create_message_result(
        chat_completion: ChatCompletion,
    ) -> CreateMessageResult:
        if len(chat_completion.choices) == 0:
            raise ValueError("No response for completion")

        first_choice = chat_completion.choices[0]

        if content := first_choice.message.content:
            return CreateMessageResult(
                content=TextContent(type="text", text=content),
                role="assistant",
                model=chat_completion.model,
            )

        raise ValueError("No content in response from completion")

    def _select_model_from_preferences(
        self, model_preferences: ModelPreferences | str | list[str] | None
    ) -> ChatModel:
        for model_option in self._iter_models_from_preferences(model_preferences):
            if model_option in get_args(ChatModel):
                chosen_model: ChatModel = model_option  # type: ignore[assignment]
                return chosen_model

        return self.default_model

    @staticmethod
    def _convert_tools_to_openai(tools: list[Tool]) -> list[ChatCompletionToolParam]:
        """Convert MCP tools to OpenAI tool format."""
        openai_tools: list[ChatCompletionToolParam] = []
        for tool in tools:
            # Build parameters dict, ensuring required fields
            parameters: dict[str, Any] = dict(tool.inputSchema)
            if "type" not in parameters:
                parameters["type"] = "object"

            openai_tools.append(
                ChatCompletionToolParam(
                    type="function",
                    function=FunctionDefinition(
                        name=tool.name,
                        description=tool.description or "",
                        parameters=parameters,
                    ),
                )
            )
        return openai_tools

    @staticmethod
    def _convert_tool_choice_to_openai(
        tool_choice: ToolChoice,
    ) -> ChatCompletionToolChoiceOptionParam:
        """Convert MCP tool_choice to OpenAI format."""
        if tool_choice.mode == "auto":
            return "auto"
        elif tool_choice.mode == "required":
            return "required"
        elif tool_choice.mode == "none":
            return "none"
        else:
            raise ValueError(f"Unsupported tool_choice mode: {tool_choice.mode!r}")

    @staticmethod
    def _chat_completion_to_result_with_tools(
        chat_completion: ChatCompletion,
    ) -> CreateMessageResultWithTools:
        """Convert OpenAI response to CreateMessageResultWithTools."""
        if len(chat_completion.choices) == 0:
            raise ValueError("No response for completion")

        first_choice = chat_completion.choices[0]
        message = first_choice.message

        # Determine stop reason
        stop_reason: StopReason
        if first_choice.finish_reason == "tool_calls":
            stop_reason = "toolUse"
        elif first_choice.finish_reason == "stop":
            stop_reason = "endTurn"
        elif first_choice.finish_reason == "length":
            stop_reason = "maxTokens"
        else:
            stop_reason = "endTurn"

        # Build content list
        content: list[TextContent | ToolUseContent] = []

        # Add text content if present
        if message.content:
            content.append(TextContent(type="text", text=message.content))

        # Add tool calls if present
        if message.tool_calls:
            for tool_call in message.tool_calls:
                # Skip non-function tool calls
                if not hasattr(tool_call, "function"):
                    continue
                func = tool_call.function
                # Parse the arguments JSON string
                try:
                    arguments = json.loads(func.arguments)  # type: ignore[union-attr]
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Invalid JSON in tool arguments for "
                        f"'{func.name}': {func.arguments}"  # type: ignore[union-attr]
                    ) from e

                content.append(
                    ToolUseContent(
                        type="tool_use",
                        id=tool_call.id,
                        name=func.name,  # type: ignore[union-attr]
                        input=arguments,
                    )
                )

        # Must have at least some content
        if not content:
            raise ValueError("No content in response from completion")

        return CreateMessageResultWithTools(
            content=content,  # type: ignore[arg-type]
            role="assistant",
            model=chat_completion.model,
            stopReason=stop_reason,
        )
