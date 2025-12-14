from unittest.mock import MagicMock

import pytest
from anthropic import AsyncAnthropic
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from mcp.types import (
    CreateMessageResult,
    CreateMessageResultWithTools,
    ModelHint,
    ModelPreferences,
    SamplingMessage,
    TextContent,
    ToolUseContent,
)

from fastmcp.client.sampling.handlers.anthropic import AnthropicSamplingHandler


def test_convert_sampling_messages_to_anthropic_messages():
    msgs = AnthropicSamplingHandler._convert_to_anthropic_messages(
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
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "ok"},
    ]


def test_convert_to_anthropic_messages_raises_on_non_text():
    from fastmcp.utilities.types import Image

    with pytest.raises(ValueError):
        AnthropicSamplingHandler._convert_to_anthropic_messages(
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
        ("claude-3-5-sonnet-20241022", "claude-3-5-sonnet-20241022"),
        (
            ModelPreferences(hints=[ModelHint(name="claude-3-5-sonnet-20241022")]),
            "claude-3-5-sonnet-20241022",
        ),
        (["claude-3-5-sonnet-20241022", "other"], "claude-3-5-sonnet-20241022"),
        (None, "fallback-model"),
        (["unknown-model"], "fallback-model"),
    ],
)
def test_select_model_from_preferences(prefs, expected):
    mock_client = MagicMock(spec=AsyncAnthropic)
    handler = AnthropicSamplingHandler(
        default_model="fallback-model", client=mock_client
    )
    assert handler._select_model_from_preferences(prefs) == expected


def test_message_to_create_message_result():
    mock_client = MagicMock(spec=AsyncAnthropic)
    handler = AnthropicSamplingHandler(
        default_model="fallback-model", client=mock_client
    )

    message = Message(
        id="msg_123",
        type="message",
        role="assistant",
        content=[TextBlock(type="text", text="HELPFUL CONTENT FROM A VERY SMART LLM")],
        model="claude-3-5-sonnet-20241022",
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=20),
    )

    result: CreateMessageResult = handler._message_to_create_message_result(message)
    assert result == CreateMessageResult(
        content=TextContent(type="text", text="HELPFUL CONTENT FROM A VERY SMART LLM"),
        role="assistant",
        model="claude-3-5-sonnet-20241022",
    )


def test_message_to_result_with_tools():
    message = Message(
        id="msg_123",
        type="message",
        role="assistant",
        content=[
            TextBlock(type="text", text="I'll help you with that."),
            ToolUseBlock(
                type="tool_use",
                id="toolu_123",
                name="get_weather",
                input={"location": "San Francisco"},
            ),
        ],
        model="claude-3-5-sonnet-20241022",
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=20),
    )

    result: CreateMessageResultWithTools = (
        AnthropicSamplingHandler._message_to_result_with_tools(message)
    )

    assert result.role == "assistant"
    assert result.model == "claude-3-5-sonnet-20241022"
    assert result.stopReason == "toolUse"
    content = result.content_as_list
    assert len(content) == 2
    assert content[0] == TextContent(type="text", text="I'll help you with that.")
    assert content[1] == ToolUseContent(
        type="tool_use",
        id="toolu_123",
        name="get_weather",
        input={"location": "San Francisco"},
    )


def test_convert_tool_choice_auto():
    result = AnthropicSamplingHandler._convert_tool_choice_to_anthropic(
        MagicMock(mode="auto")
    )
    assert result is not None
    assert result["type"] == "auto"


def test_convert_tool_choice_required():
    result = AnthropicSamplingHandler._convert_tool_choice_to_anthropic(
        MagicMock(mode="required")
    )
    assert result is not None
    assert result["type"] == "any"


def test_convert_tool_choice_none():
    result = AnthropicSamplingHandler._convert_tool_choice_to_anthropic(
        MagicMock(mode="none")
    )
    # Anthropic doesn't have "none", returns None to signal tools should be omitted
    assert result is None


def test_convert_tool_choice_unknown_raises():
    with pytest.raises(ValueError, match="Unsupported tool_choice mode"):
        AnthropicSamplingHandler._convert_tool_choice_to_anthropic(
            MagicMock(mode="unknown")
        )


def test_convert_tools_to_anthropic():
    from mcp.types import Tool

    tools = [
        Tool(
            name="get_weather",
            description="Get the current weather",
            inputSchema={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        )
    ]

    result = AnthropicSamplingHandler._convert_tools_to_anthropic(tools)

    assert len(result) == 1
    assert result[0]["name"] == "get_weather"
    assert result[0]["description"] == "Get the current weather"
    assert result[0]["input_schema"] == {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    }


def test_convert_messages_with_tool_use_content():
    """Test converting messages that include tool use content from assistant."""
    msgs = AnthropicSamplingHandler._convert_to_anthropic_messages(
        messages=[
            SamplingMessage(
                role="assistant",
                content=ToolUseContent(
                    type="tool_use",
                    id="toolu_123",
                    name="get_weather",
                    input={"location": "NYC"},
                ),
            ),
        ],
    )

    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_123",
            "name": "get_weather",
            "input": {"location": "NYC"},
        }
    ]


def test_convert_messages_with_tool_result_content():
    """Test converting messages that include tool result content from user."""
    from mcp.types import ToolResultContent

    msgs = AnthropicSamplingHandler._convert_to_anthropic_messages(
        messages=[
            SamplingMessage(
                role="user",
                content=ToolResultContent(
                    type="tool_result",
                    toolUseId="toolu_123",
                    content=[TextContent(type="text", text="72F and sunny")],
                ),
            ),
        ],
    )

    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_123",
            "content": "72F and sunny",
            "is_error": False,
        }
    ]
