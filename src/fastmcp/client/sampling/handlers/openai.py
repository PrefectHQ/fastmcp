"""OpenAI sampling handler for FastMCP."""

import json
from collections.abc import Iterator, Sequence
from typing import Any, get_args

from mcp import ClientSession, ServerSession
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import (
    AudioContent,
    CreateMessageResult,
    CreateMessageResultWithTools,
    ImageContent,
    ModelPreferences,
    SamplingMessage,
    StopReason,
    TextContent,
    Tool,
    ToolChoice,
    ToolResultContent,
    ToolUseContent,
)
from mcp.types import CreateMessageRequestParams as SamplingParams

try:
    from openai import AsyncOpenAI
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionAssistantMessageParam,
        ChatCompletionContentPartImageParam,
        ChatCompletionContentPartInputAudioParam,
        ChatCompletionContentPartParam,
        ChatCompletionContentPartTextParam,
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

# OpenAI only supports wav and mp3 for input audio
_OPENAI_AUDIO_FORMATS: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp3": "mp3",
    "audio/mpeg": "mp3",
}

_OPENAI_IMAGE_MEDIA_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)


def _image_content_to_openai_part(
    content: ImageContent,
) -> ChatCompletionContentPartImageParam:
    """Convert MCP ImageContent to OpenAI image_url content part."""
    if content.mimeType not in _OPENAI_IMAGE_MEDIA_TYPES:
        raise ValueError(
            f"Unsupported image MIME type for OpenAI: {content.mimeType!r}. "
            f"Supported types: {', '.join(sorted(_OPENAI_IMAGE_MEDIA_TYPES))}"
        )
    data_url = f"data:{content.mimeType};base64,{content.data}"
    return ChatCompletionContentPartImageParam(
        type="image_url",
        image_url={"url": data_url},
    )


def _audio_content_to_openai_part(
    content: AudioContent,
) -> ChatCompletionContentPartInputAudioParam:
    """Convert MCP AudioContent to OpenAI input_audio content part."""
    audio_format = _OPENAI_AUDIO_FORMATS.get(content.mimeType)
    if audio_format is None:
        raise ValueError(
            f"Unsupported audio MIME type for OpenAI: {content.mimeType!r}. "
            f"Supported types: {', '.join(sorted(_OPENAI_AUDIO_FORMATS))}"
        )
    return ChatCompletionContentPartInputAudioParam(
        type="input_audio",
        input_audio={"data": content.data, "format": audio_format},
    )


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
        openai_tools: list[ChatCompletionToolParam] | None = None
        if params.tools:
            openai_tools = self._convert_tools_to_openai(params.tools)

        # Convert tool_choice to OpenAI format
        openai_tool_choice: ChatCompletionToolChoiceOptionParam | None = None
        if params.toolChoice:
            openai_tool_choice = self._convert_tool_choice_to_openai(params.toolChoice)

        # Build kwargs to avoid sentinel type compatibility issues across
        # openai SDK versions (NotGiven vs Omit)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
        }
        if params.maxTokens is not None:
            kwargs["max_completion_tokens"] = params.maxTokens
        if params.temperature is not None:
            kwargs["temperature"] = params.temperature
        if params.stopSequences:
            kwargs["stop"] = params.stopSequences
        if openai_tools is not None:
            kwargs["tools"] = openai_tools
        if openai_tool_choice is not None:
            kwargs["tool_choice"] = openai_tool_choice

        response = await self.client.chat.completions.create(**kwargs)

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
                # Collect tool calls, content parts, and text from the list
                tool_calls: list[ChatCompletionMessageToolCallParam] = []
                content_parts: list[ChatCompletionContentPartParam] = []
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
                        content_parts.append(
                            ChatCompletionContentPartTextParam(
                                type="text", text=item.text
                            )
                        )
                    elif isinstance(item, ImageContent):
                        content_parts.append(_image_content_to_openai_part(item))
                    elif isinstance(item, AudioContent):
                        content_parts.append(_audio_content_to_openai_part(item))
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
                if tool_calls or content_parts:
                    if tool_calls:
                        has_multimodal = len(content_parts) > len(text_parts)
                        if has_multimodal:
                            raise ValueError(
                                "ImageContent/AudioContent is only supported "
                                "in user messages for OpenAI"
                            )
                        text_str = "\n".join(text_parts) or None
                        openai_messages.append(
                            ChatCompletionAssistantMessageParam(
                                role="assistant",
                                content=text_str,
                                tool_calls=tool_calls,
                            )
                        )
                        # Add tool messages AFTER assistant message
                        openai_messages.extend(tool_messages)
                    elif content_parts:
                        if message.role == "user":
                            openai_messages.append(
                                ChatCompletionUserMessageParam(
                                    role="user",
                                    content=content_parts,
                                )
                            )
                        else:
                            has_multimodal = len(content_parts) > len(text_parts)
                            if has_multimodal:
                                raise ValueError(
                                    "ImageContent/AudioContent is only supported "
                                    "in user messages for OpenAI"
                                )
                            assistant_text = "\n".join(text_parts)
                            if assistant_text:
                                openai_messages.append(
                                    ChatCompletionAssistantMessageParam(
                                        role="assistant",
                                        content=assistant_text,
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

            # Handle ImageContent
            if isinstance(content, ImageContent):
                if message.role != "user":
                    raise ValueError(
                        "ImageContent is only supported in user messages for OpenAI"
                    )
                openai_messages.append(
                    ChatCompletionUserMessageParam(
                        role="user",
                        content=[_image_content_to_openai_part(content)],
                    )
                )
                continue

            # Handle AudioContent
            if isinstance(content, AudioContent):
                if message.role != "user":
                    raise ValueError(
                        "AudioContent is only supported in user messages for OpenAI"
                    )
                openai_messages.append(
                    ChatCompletionUserMessageParam(
                        role="user",
                        content=[_audio_content_to_openai_part(content)],
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
                chosen_model: ChatModel = model_option  # type: ignore[assignment]  # ty:ignore[invalid-assignment]
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
                    arguments = json.loads(func.arguments)  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"Invalid JSON in tool arguments for "
                        f"'{func.name}': {func.arguments}"  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
                    ) from e

                content.append(
                    ToolUseContent(
                        type="tool_use",
                        id=tool_call.id,
                        name=func.name,  # type: ignore[union-attr]  # ty:ignore[unresolved-attribute]
                        input=arguments,
                    )
                )

        # Must have at least some content
        if not content:
            raise ValueError("No content in response from completion")

        return CreateMessageResultWithTools(
            content=content,  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
            role="assistant",
            model=chat_completion.model,
            stopReason=stop_reason,
        )
