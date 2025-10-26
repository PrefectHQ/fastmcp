from collections.abc import Iterator, Sequence
from typing import get_args

from mcp import ClientSession, ServerSession
from mcp.shared.context import LifespanContextT, RequestContext
from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import (
    CreateMessageResult,
    ModelPreferences,
    SamplingMessage,
    TextContent,
)

try:
    from openai import NOT_GIVEN, OpenAI
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionAssistantMessageParam,
        ChatCompletionMessageParam,
        ChatCompletionSystemMessageParam,
        ChatCompletionUserMessageParam,
    )
    from openai.types.shared.chat_model import ChatModel
except ImportError as e:
    raise ImportError(
        "The `openai` package is not installed. Please install `fastmcp[openai]` or add `openai` to your dependencies manually."
    ) from e

from typing_extensions import override

from fastmcp.experimental.sampling.handlers.base import BaseLLMSamplingHandler


class OpenAISamplingHandler(BaseLLMSamplingHandler):
    def __init__(self, default_model: ChatModel, client: OpenAI | None = None):
        self.client: OpenAI = client or OpenAI()
        self.default_model: ChatModel = default_model

    @override
    async def __call__(
        self,
        messages: list[SamplingMessage],
        params: SamplingParams,
        context: RequestContext[ServerSession, LifespanContextT]
        | RequestContext[ClientSession, LifespanContextT],
    ) -> CreateMessageResult:
        openai_messages: list[ChatCompletionMessageParam] = (
            self._convert_to_openai_messages(
                system_prompt=params.systemPrompt,
                messages=messages,
            )
        )

        model: ChatModel = self._select_model_from_preferences(params.modelPreferences)

        response = self.client.chat.completions.create(
            model=model,
            messages=openai_messages,
            temperature=params.temperature or NOT_GIVEN,
            max_tokens=params.maxTokens,
            stop=params.stopSequences or NOT_GIVEN,
        )

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

        if isinstance(model_preferences, list):
            yield from model_preferences

        if isinstance(model_preferences, ModelPreferences):
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

        if isinstance(messages, str):
            openai_messages.append(
                ChatCompletionUserMessageParam(
                    role="user",
                    content=messages,
                )
            )

        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, str):
                    openai_messages.append(
                        ChatCompletionUserMessageParam(
                            role="user",
                            content=message,
                        )
                    )
                    continue

                if not isinstance(message.content, TextContent):
                    raise ValueError("Only text content is supported")

                if message.role == "user":
                    openai_messages.append(
                        ChatCompletionUserMessageParam(
                            role="user",
                            content=message.content.text,
                        )
                    )
                else:
                    openai_messages.append(
                        ChatCompletionAssistantMessageParam(
                            role="assistant",
                            content=message.content.text,
                        )
                    )

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
                chosen_model: ChatModel = model_option  # pyright: ignore[reportAssignmentType]
                return chosen_model

        return self.default_model
