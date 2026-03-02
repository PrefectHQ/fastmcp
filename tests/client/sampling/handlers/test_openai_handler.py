from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    ModelHint,
    ModelPreferences,
    SamplingMessage,
    TextContent,
)
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessage,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion import Choice

from fastmcp.client.sampling.handlers.openai import OpenAISamplingHandler


def test_convert_sampling_messages_to_openai_messages():
    msgs = OpenAISamplingHandler._convert_to_openai_messages(
        system_prompt="sys",
        messages=[
            SamplingMessage(
                role="user", content=TextContent(type="text", text="hello")
            ),
            SamplingMessage(
                role="assistant", content=TextContent(type="text", text="ok")
            ),
        ],
    )

    assert msgs == [
        ChatCompletionSystemMessageParam(content="sys", role="system"),
        ChatCompletionUserMessageParam(content="hello", role="user"),
        ChatCompletionAssistantMessageParam(content="ok", role="assistant"),
    ]


def test_convert_to_openai_messages_raises_on_non_text():
    from fastmcp.utilities.types import Image

    with pytest.raises(ValueError):
        OpenAISamplingHandler._convert_to_openai_messages(
            system_prompt=None,
            messages=[
                SamplingMessage(
                    role="user",
                    content=Image(data=b"abc").to_image_content(),
                )
            ],
        )


@pytest.mark.parametrize(
    "prefs,expected",
    [
        ("gpt-4o-mini", "gpt-4o-mini"),
        (ModelPreferences(hints=[ModelHint(name="gpt-4o-mini")]), "gpt-4o-mini"),
        (["gpt-4o-mini", "other"], "gpt-4o-mini"),
        (None, "fallback-model"),
        (["unknown-model"], "fallback-model"),
    ],
)
def test_select_model_from_preferences(prefs, expected):
    mock_client = MagicMock(spec=AsyncOpenAI)
    handler = OpenAISamplingHandler(default_model="fallback-model", client=mock_client)  # type: ignore[arg-type]
    assert handler._select_model_from_preferences(prefs) == expected


async def test_handler_passes_max_completion_tokens():
    """Verify the handler uses max_completion_tokens (not max_tokens)."""
    mock_client = MagicMock(spec=AsyncOpenAI)
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=ChatCompletion(
            id="123",
            created=123,
            model="gpt-4o-mini",
            object="chat.completion",
            choices=[
                Choice(
                    message=ChatCompletionMessage(content="hi", role="assistant"),
                    finish_reason="stop",
                    index=0,
                )
            ],
        )
    )
    handler = OpenAISamplingHandler(default_model="gpt-4o-mini", client=mock_client)
    messages = [
        SamplingMessage(role="user", content=TextContent(type="text", text="hello"))
    ]
    params = CreateMessageRequestParams(messages=messages, maxTokens=300)
    await handler(messages, params, context=None)  # type: ignore[arg-type]

    call_kwargs = mock_client.chat.completions.create.call_args
    assert "max_completion_tokens" in call_kwargs.kwargs
    assert call_kwargs.kwargs["max_completion_tokens"] == 300
    assert "max_tokens" not in call_kwargs.kwargs


async def test_chat_completion_to_create_message_result():
    mock_client = MagicMock(spec=AsyncOpenAI)
    handler = OpenAISamplingHandler(default_model="fallback-model", client=mock_client)  # type: ignore[arg-type]
    mock_client.chat.completions.create.return_value = ChatCompletion(
        id="123",
        created=123,
        model="gpt-4o-mini",
        object="chat.completion",
        choices=[
            Choice(
                message=ChatCompletionMessage(
                    content="HELPFUL CONTENT FROM A VERY SMART LLM", role="assistant"
                ),
                finish_reason="stop",
                index=0,
            )
        ],
    )
    result: CreateMessageResult = handler._chat_completion_to_create_message_result(
        chat_completion=mock_client.chat.completions.create.return_value
    )
    assert result == CreateMessageResult(
        content=TextContent(type="text", text="HELPFUL CONTENT FROM A VERY SMART LLM"),
        role="assistant",
        model="gpt-4o-mini",
    )
