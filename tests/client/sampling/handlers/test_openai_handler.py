from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import (
    AudioContent,
    CreateMessageRequestParams,
    CreateMessageResult,
    ImageContent,
    ModelHint,
    ModelPreferences,
    SamplingMessage,
    TextContent,
    ToolUseContent,
)
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartInputAudioParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionMessage,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion import Choice

from fastmcp.client.sampling.handlers.openai import (
    OpenAISamplingHandler,
    _audio_content_to_openai_part,
    _image_content_to_openai_part,
)


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


def test_image_content_to_openai_part():
    part = _image_content_to_openai_part(
        ImageContent(type="image", data="YWJj", mimeType="image/png")
    )

    assert part == ChatCompletionContentPartImageParam(
        type="image_url",
        image_url={"url": "data:image/png;base64,YWJj"},
    )


def test_audio_content_to_openai_part_wav():
    part = _audio_content_to_openai_part(
        AudioContent(type="audio", data="YWJj", mimeType="audio/wav")
    )

    assert part == ChatCompletionContentPartInputAudioParam(
        type="input_audio",
        input_audio={"data": "YWJj", "format": "wav"},
    )


def test_audio_content_to_openai_part_mp3():
    part = _audio_content_to_openai_part(
        AudioContent(type="audio", data="YWJj", mimeType="audio/mpeg")
    )

    assert part["input_audio"]["format"] == "mp3"


def test_audio_content_to_openai_part_unsupported_raises():
    with pytest.raises(ValueError, match="Unsupported audio MIME type"):
        _audio_content_to_openai_part(
            AudioContent(type="audio", data="YWJj", mimeType="audio/ogg")
        )


def test_image_content_to_openai_part_unsupported_raises():
    with pytest.raises(ValueError, match="Unsupported image MIME type"):
        _image_content_to_openai_part(
            ImageContent(type="image", data="YWJj", mimeType="image/bmp")
        )


def test_convert_single_image_content_to_openai_message():
    msgs = OpenAISamplingHandler._convert_to_openai_messages(
        system_prompt=None,
        messages=[
            SamplingMessage(
                role="user",
                content=ImageContent(type="image", data="YWJj", mimeType="image/png"),
            )
        ],
    )

    assert len(msgs) == 1
    assert msgs[0] == ChatCompletionUserMessageParam(
        role="user",
        content=[
            ChatCompletionContentPartImageParam(
                type="image_url",
                image_url={"url": "data:image/png;base64,YWJj"},
            )
        ],
    )


def test_convert_single_audio_content_to_openai_message():
    msgs = OpenAISamplingHandler._convert_to_openai_messages(
        system_prompt=None,
        messages=[
            SamplingMessage(
                role="user",
                content=AudioContent(type="audio", data="YWJj", mimeType="audio/wav"),
            )
        ],
    )

    assert len(msgs) == 1
    assert msgs[0] == ChatCompletionUserMessageParam(
        role="user",
        content=[
            ChatCompletionContentPartInputAudioParam(
                type="input_audio",
                input_audio={"data": "YWJj", "format": "wav"},
            )
        ],
    )


def test_convert_list_content_with_image_and_text():
    msgs = OpenAISamplingHandler._convert_to_openai_messages(
        system_prompt=None,
        messages=[
            SamplingMessage(
                role="user",
                content=[
                    TextContent(type="text", text="What is in this image?"),
                    ImageContent(type="image", data="YWJj", mimeType="image/jpeg"),
                ],
            )
        ],
    )

    assert len(msgs) == 1
    assert msgs[0] == ChatCompletionUserMessageParam(
        role="user",
        content=[
            ChatCompletionContentPartTextParam(
                type="text", text="What is in this image?"
            ),
            ChatCompletionContentPartImageParam(
                type="image_url",
                image_url={"url": "data:image/jpeg;base64,YWJj"},
            ),
        ],
    )


def test_convert_image_in_assistant_message_raises():
    with pytest.raises(ValueError, match="ImageContent is only supported in user"):
        OpenAISamplingHandler._convert_to_openai_messages(
            system_prompt=None,
            messages=[
                SamplingMessage(
                    role="assistant",
                    content=ImageContent(
                        type="image", data="YWJj", mimeType="image/png"
                    ),
                )
            ],
        )


def test_convert_audio_in_assistant_message_raises():
    with pytest.raises(ValueError, match="AudioContent is only supported in user"):
        OpenAISamplingHandler._convert_to_openai_messages(
            system_prompt=None,
            messages=[
                SamplingMessage(
                    role="assistant",
                    content=AudioContent(
                        type="audio", data="YWJj", mimeType="audio/wav"
                    ),
                )
            ],
        )


def test_convert_list_image_in_assistant_message_raises():
    """Image/audio in an assistant list-content message should raise, not silently drop."""
    with pytest.raises(ValueError, match="only supported in user messages"):
        OpenAISamplingHandler._convert_to_openai_messages(
            system_prompt=None,
            messages=[
                SamplingMessage(
                    role="assistant",
                    content=[
                        TextContent(type="text", text="Here's the image"),
                        ImageContent(type="image", data="YWJj", mimeType="image/png"),
                    ],
                )
            ],
        )


def test_convert_list_tool_calls_with_image_raises():
    """Image/audio alongside tool_calls in assistant list should raise."""
    with pytest.raises(ValueError, match="only supported in user messages"):
        OpenAISamplingHandler._convert_to_openai_messages(
            system_prompt=None,
            messages=[
                SamplingMessage(
                    role="assistant",
                    content=[
                        ToolUseContent(
                            type="tool_use",
                            id="call_1",
                            name="my_tool",
                            input={"arg": "val"},
                        ),
                        ImageContent(type="image", data="YWJj", mimeType="image/png"),
                    ],
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
def test_select_model_from_preferences(prefs: Any, expected: str) -> None:
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
