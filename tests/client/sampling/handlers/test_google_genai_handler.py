import base64
from unittest.mock import MagicMock

import pytest

try:
    from google.genai import Client as GoogleGenaiClient
    from google.genai.types import (
        Candidate,
        FunctionCall,
        FunctionCallingConfigMode,
        GenerateContentResponse,
        ModelContent,
        Part,
        UserContent,
    )
    from mcp.types import (
        AudioContent,
        CreateMessageResult,
        ImageContent,
        ModelHint,
        ModelPreferences,
        SamplingMessage,
        TextContent,
        ToolChoice,
        ToolResultContent,
        ToolUseContent,
    )

    from fastmcp.client.sampling.handlers.google_genai import (
        GoogleGenaiSamplingHandler,
        _convert_messages_to_google_genai_content,
        _convert_tool_choice_to_google_genai,
        _response_to_create_message_result,
        _response_to_result_with_tools,
        _sampling_content_to_google_genai_part,
    )

    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not GOOGLE_GENAI_AVAILABLE, reason="google-genai not installed"
)


def test_convert_sampling_messages_to_google_genai_content():
    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user", content=TextContent(type="text", text="hello")
            ),
            SamplingMessage(
                role="assistant", content=TextContent(type="text", text="ok")
            ),
        ],
    )

    assert len(msgs) == 2
    assert isinstance(msgs[0], UserContent)
    assert isinstance(msgs[1], ModelContent)
    assert msgs[0].parts[0].text == "hello"
    assert msgs[1].parts[0].text == "ok"


def test_convert_single_image_content_to_google_genai():
    part = _sampling_content_to_google_genai_part(
        ImageContent(type="image", data="YWJj", mimeType="image/png")
    )

    assert part.inline_data is not None
    assert part.inline_data.data == base64.b64decode("YWJj")
    assert part.inline_data.mime_type == "image/png"


def test_convert_single_audio_content_to_google_genai():
    part = _sampling_content_to_google_genai_part(
        AudioContent(type="audio", data="YWJj", mimeType="audio/wav")
    )

    assert part.inline_data is not None
    assert part.inline_data.data == base64.b64decode("YWJj")
    assert part.inline_data.mime_type == "audio/wav"


def test_convert_image_message_to_google_genai_content():
    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user",
                content=ImageContent(type="image", data="YWJj", mimeType="image/jpeg"),
            )
        ],
    )

    assert len(msgs) == 1
    assert isinstance(msgs[0], UserContent)
    assert msgs[0].parts[0].inline_data is not None
    assert msgs[0].parts[0].inline_data.mime_type == "image/jpeg"


def test_convert_audio_message_to_google_genai_content():
    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user",
                content=AudioContent(type="audio", data="YWJj", mimeType="audio/mp3"),
            )
        ],
    )

    assert len(msgs) == 1
    assert isinstance(msgs[0], UserContent)
    assert msgs[0].parts[0].inline_data is not None
    assert msgs[0].parts[0].inline_data.mime_type == "audio/mp3"


def test_convert_list_content_with_image_and_text():
    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user",
                content=[
                    TextContent(type="text", text="What is in this image?"),
                    ImageContent(type="image", data="YWJj", mimeType="image/png"),
                ],
            )
        ],
    )

    assert len(msgs) == 1
    assert isinstance(msgs[0], UserContent)
    assert len(msgs[0].parts) == 2
    assert msgs[0].parts[0].text == "What is in this image?"
    assert msgs[0].parts[1].inline_data is not None
    assert msgs[0].parts[1].inline_data.mime_type == "image/png"


def test_convert_list_content_with_audio_and_text():
    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user",
                content=[
                    TextContent(type="text", text="Transcribe this audio"),
                    AudioContent(type="audio", data="YWJj", mimeType="audio/wav"),
                ],
            )
        ],
    )

    assert len(msgs) == 1
    assert isinstance(msgs[0], UserContent)
    assert len(msgs[0].parts) == 2
    assert msgs[0].parts[0].text == "Transcribe this audio"
    assert msgs[0].parts[1].inline_data is not None
    assert msgs[0].parts[1].inline_data.mime_type == "audio/wav"


def test_get_model():
    mock_client = MagicMock(spec=GoogleGenaiClient)
    handler = GoogleGenaiSamplingHandler(
        default_model="fallback-model", client=mock_client
    )

    # Test with Gemini model hint
    prefs = ModelPreferences(hints=[ModelHint(name="gemini-2.0-flash-exp")])
    assert handler._get_model(prefs) == "gemini-2.0-flash-exp"

    # Test with None
    assert handler._get_model(None) == "fallback-model"

    # Test with empty hints
    prefs_empty = ModelPreferences(hints=[])
    assert handler._get_model(prefs_empty) == "fallback-model"

    # Test with non-Gemini hint falls back to default
    prefs_other = ModelPreferences(hints=[ModelHint(name="gpt-4o")])
    assert handler._get_model(prefs_other) == "fallback-model"

    # Test with mixed hints selects first Gemini model
    prefs_mixed = ModelPreferences(
        hints=[ModelHint(name="claude-3.5-sonnet"), ModelHint(name="gemini-2.0-flash")]
    )
    assert handler._get_model(prefs_mixed) == "gemini-2.0-flash"


async def test_response_to_create_message_result():
    # Create a mock response
    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.text = "HELPFUL CONTENT FROM GEMINI"

    result: CreateMessageResult = _response_to_create_message_result(
        response=mock_response, model="gemini-2.0-flash-exp"
    )
    assert result == CreateMessageResult(
        content=TextContent(type="text", text="HELPFUL CONTENT FROM GEMINI"),
        role="assistant",
        model="gemini-2.0-flash-exp",
    )


def test_convert_tool_choice_to_google_genai():
    # Test auto mode
    result = _convert_tool_choice_to_google_genai(ToolChoice(mode="auto"))
    assert result.function_calling_config is not None
    assert result.function_calling_config.mode == FunctionCallingConfigMode.AUTO

    # Test required mode
    result = _convert_tool_choice_to_google_genai(ToolChoice(mode="required"))
    assert result.function_calling_config is not None
    assert result.function_calling_config.mode == FunctionCallingConfigMode.ANY

    # Test none mode
    result = _convert_tool_choice_to_google_genai(ToolChoice(mode="none"))
    assert result.function_calling_config is not None
    assert result.function_calling_config.mode == FunctionCallingConfigMode.NONE

    # Test None (defaults to auto)
    result = _convert_tool_choice_to_google_genai(None)
    assert result.function_calling_config is not None
    assert result.function_calling_config.mode == FunctionCallingConfigMode.AUTO


def test_sampling_content_to_google_genai_part_tool_use():
    """Test converting ToolUseContent to Google GenAI Part with FunctionCall."""
    content = ToolUseContent(
        type="tool_use",
        id="get_weather_abc123",
        name="get_weather",
        input={"city": "London"},
    )

    part = _sampling_content_to_google_genai_part(content)

    assert part.function_call is not None
    assert part.function_call.name == "get_weather"
    assert part.function_call.args == {"city": "London"}


def test_sampling_content_to_google_genai_part_tool_result():
    """Test converting ToolResultContent to Google GenAI Part with FunctionResponse."""
    content = ToolResultContent(
        type="tool_result",
        toolUseId="get_weather_abc123",
        content=[TextContent(type="text", text="Weather is sunny")],
    )

    part = _sampling_content_to_google_genai_part(content)

    assert part.function_response is not None
    # Function name is extracted from toolUseId by removing the UUID suffix
    assert part.function_response.name == "get_weather"
    assert part.function_response.response == {"result": "Weather is sunny"}


def test_sampling_content_to_google_genai_part_tool_result_empty():
    """Test converting empty ToolResultContent to Google GenAI Part."""
    content = ToolResultContent(
        type="tool_result",
        toolUseId="my_tool_xyz789",
        content=[],
    )

    part = _sampling_content_to_google_genai_part(content)

    assert part.function_response is not None
    assert part.function_response.name == "my_tool"
    assert part.function_response.response == {"result": ""}


def test_sampling_content_to_google_genai_part_tool_result_no_underscore():
    """Test ToolResultContent when toolUseId has no underscore (fallback)."""
    content = ToolResultContent(
        type="tool_result",
        toolUseId="simplefunction",
        content=[TextContent(type="text", text="Result")],
    )

    part = _sampling_content_to_google_genai_part(content)

    # When no underscore, the full ID is used as the name
    assert part.function_response is not None
    assert part.function_response.name == "simplefunction"


def test_convert_messages_with_tool_use():
    """Test converting messages containing ToolUseContent."""
    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(type="text", text="What's the weather?"),
            ),
            SamplingMessage(
                role="assistant",
                content=ToolUseContent(
                    type="tool_use",
                    id="get_weather_123",
                    name="get_weather",
                    input={"city": "NYC"},
                ),
            ),
        ],
    )

    assert len(msgs) == 2
    assert isinstance(msgs[0], UserContent)
    assert isinstance(msgs[1], ModelContent)
    assert msgs[1].parts[0].function_call is not None
    assert msgs[1].parts[0].function_call.name == "get_weather"


def test_convert_messages_with_tool_result():
    """Test converting messages containing ToolResultContent."""
    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user",
                content=ToolResultContent(
                    type="tool_result",
                    toolUseId="get_weather_123",
                    content=[TextContent(type="text", text="Sunny, 72F")],
                ),
            ),
        ],
    )

    assert len(msgs) == 1
    assert isinstance(msgs[0], UserContent)
    assert msgs[0].parts[0].function_response is not None
    assert msgs[0].parts[0].function_response.name == "get_weather"


def test_convert_messages_with_multiple_content_blocks():
    """Test converting messages with multiple content blocks (list content)."""
    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user",
                content=[
                    TextContent(type="text", text="I need weather info."),
                    ToolResultContent(
                        type="tool_result",
                        toolUseId="get_weather_xyz",
                        content=[TextContent(type="text", text="Cloudy")],
                    ),
                ],
            ),
        ],
    )

    assert len(msgs) == 1
    assert isinstance(msgs[0], UserContent)
    assert len(msgs[0].parts) == 2
    assert msgs[0].parts[0].text == "I need weather info."
    assert msgs[0].parts[1].function_response is not None


def test_response_to_result_with_tools_text_only():
    """Test _response_to_result_with_tools with a text-only response."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = [Part(text="Here's the answer")]
    mock_candidate.finish_reason = "STOP"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.candidates = [mock_candidate]

    result = _response_to_result_with_tools(mock_response, model="gemini-2.0-flash")

    assert result.role == "assistant"
    assert result.model == "gemini-2.0-flash"
    assert result.stopReason == "endTurn"
    assert isinstance(result.content, list)
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Here's the answer"


def test_response_to_result_with_tools_function_call():
    """Test _response_to_result_with_tools with a function call response."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = [
        Part(function_call=FunctionCall(name="get_weather", args={"city": "Paris"}))
    ]
    mock_candidate.finish_reason = "STOP"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.candidates = [mock_candidate]

    result = _response_to_result_with_tools(mock_response, model="gemini-2.0-flash")

    assert result.stopReason == "toolUse"
    assert isinstance(result.content, list)
    assert len(result.content) == 1
    tool_use = result.content[0]
    assert isinstance(tool_use, ToolUseContent)
    assert tool_use.type == "tool_use"
    assert tool_use.name == "get_weather"
    assert tool_use.input == {"city": "Paris"}
    # ID should be in format "get_weather_{uuid}"
    assert tool_use.id.startswith("get_weather_")


def test_response_to_result_with_tools_mixed_content():
    """Test _response_to_result_with_tools with text and function call."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = [
        Part(text="Let me check that for you."),
        Part(function_call=FunctionCall(name="search", args={"query": "test"})),
    ]
    mock_candidate.finish_reason = "STOP"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.candidates = [mock_candidate]

    result = _response_to_result_with_tools(mock_response, model="gemini-2.0-flash")

    assert result.stopReason == "toolUse"
    assert isinstance(result.content, list)
    assert len(result.content) == 2
    text_content = result.content[0]
    assert isinstance(text_content, TextContent)
    assert text_content.type == "text"
    assert text_content.text == "Let me check that for you."
    tool_use = result.content[1]
    assert isinstance(tool_use, ToolUseContent)
    assert tool_use.type == "tool_use"
    assert tool_use.name == "search"


# ────────────────────────────────────────────────────────────
# Test for title stripping (PR #3860)
# ────────────────────────────────────────────────────────────


def test_convert_tool_strips_titles():
    """_convert_tool_to_google_genai should strip titles from inputSchema.

    We test via compress_schema directly rather than constructing a
    FunctionDeclaration because older google-genai versions may not
    support the parameters_json_schema field.
    """
    from fastmcp.utilities.json_schema import compress_schema

    input_schema = {
        "title": "Params",
        "type": "object",
        "properties": {
            "query": {"title": "Query", "type": "string"},
        },
    }

    schema = compress_schema(input_schema, prune_titles=True)
    assert "title" not in schema
    assert "title" not in schema["properties"]["query"]
    assert schema["properties"]["query"]["type"] == "string"


# ────────────────────────────────────────────────────────────
# Tests for thought-part filtering and improved error messages
# (PR #3849)
# ────────────────────────────────────────────────────────────


def test_thought_parts_filtered_on_tool_path():
    """Thought parts should be excluded; only the real text part should appear."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = [
        Part(text="thinking about the problem...", thought=True),
        Part(text="Here is the real answer"),
    ]
    mock_candidate.finish_reason = "STOP"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.candidates = [mock_candidate]

    result = _response_to_result_with_tools(mock_response, model="gemini-2.5-flash")

    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Here is the real answer"


def test_thought_only_response_on_tool_path_raises():
    """When the response contains ONLY thought parts the tool path should raise
    a ValueError whose message includes the finish_reason."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = [
        Part(text="deep thinking...", thought=True),
    ]
    mock_candidate.finish_reason = "STOP"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.candidates = [mock_candidate]

    with pytest.raises(ValueError, match="finish_reason=STOP"):
        _response_to_result_with_tools(mock_response, model="gemini-2.5-flash")


def test_thought_only_response_on_non_tool_path_raises():
    """When the non-tool path receives only thoughts the error message should
    mention 'thinking/reasoning content'."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = [
        Part(text="internal reasoning...", thought=True),
    ]
    mock_candidate.finish_reason = "STOP"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.text = None  # No real text available
    mock_response.candidates = [mock_candidate]

    with pytest.raises(ValueError, match="thinking/reasoning content"):
        _response_to_create_message_result(mock_response, model="gemini-2.5-flash")


def test_safety_filtered_response_on_tool_path_raises():
    """A safety-filtered response (no parts) should raise with the finish_reason
    included in the error message."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = []  # Empty parts after safety filtering
    mock_candidate.finish_reason = "SAFETY"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.candidates = [mock_candidate]

    with pytest.raises(ValueError, match="finish_reason=SAFETY"):
        _response_to_result_with_tools(mock_response, model="gemini-2.5-flash")


def test_safety_filtered_response_on_non_tool_path_raises():
    """A safety-filtered response on the non-tool path should include
    finish_reason in the error."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = []
    mock_candidate.finish_reason = "SAFETY"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.text = None
    mock_response.candidates = [mock_candidate]

    with pytest.raises(ValueError, match="finish_reason=SAFETY"):
        _response_to_create_message_result(mock_response, model="gemini-2.5-flash")


def test_normal_response_text_and_function_call():
    """A normal response with both real text and a function call should
    produce both TextContent and ToolUseContent in the result."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = [
        Part(text="Let me look that up."),
        Part(function_call=FunctionCall(name="lookup", args={"q": "test"})),
    ]
    mock_candidate.finish_reason = "STOP"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.candidates = [mock_candidate]

    result = _response_to_result_with_tools(mock_response, model="gemini-2.5-flash")

    assert len(result.content) == 2
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Let me look that up."
    assert isinstance(result.content[1], ToolUseContent)
    assert result.content[1].name == "lookup"
    assert result.content[1].input == {"q": "test"}
    assert result.stopReason == "toolUse"


def test_thought_with_function_call_keeps_function_call():
    """When thinking parts accompany a function call, only the function call
    should appear in the content (thought parts filtered out)."""
    mock_candidate = MagicMock(spec=Candidate)
    mock_candidate.content = MagicMock()
    mock_candidate.content.parts = [
        Part(text="reasoning about what tool to use...", thought=True),
        Part(function_call=FunctionCall(name="get_weather", args={"city": "NYC"})),
    ]
    mock_candidate.finish_reason = "STOP"

    mock_response = MagicMock(spec=GenerateContentResponse)
    mock_response.candidates = [mock_candidate]

    result = _response_to_result_with_tools(mock_response, model="gemini-2.5-flash")

    assert len(result.content) == 1
    assert isinstance(result.content[0], ToolUseContent)
    assert result.content[0].name == "get_weather"
    assert result.stopReason == "toolUse"
