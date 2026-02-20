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
        CreateMessageResult,
        ModelHint,
        ModelPreferences,
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
    from mcp.types import SamplingMessage, TextContent

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


def test_convert_to_google_genai_messages_raises_on_non_text():
    from mcp.types import SamplingMessage

    from fastmcp.utilities.types import Image

    with pytest.raises(ValueError):
        _convert_messages_to_google_genai_content(
            messages=[
                SamplingMessage(
                    role="user",
                    content=Image(data=b"abc").to_image_content(),
                )
            ],
        )


def test_get_model():
    mock_client = MagicMock(spec=GoogleGenaiClient)
    handler = GoogleGenaiSamplingHandler(
        default_model="fallback-model", client=mock_client
    )

    # Test with model hint
    prefs = ModelPreferences(hints=[ModelHint(name="gemini-2.0-flash-exp")])
    assert handler._get_model(prefs) == "gemini-2.0-flash-exp"

    # Test with None
    assert handler._get_model(None) == "fallback-model"

    # Test with empty hints
    prefs_empty = ModelPreferences(hints=[])
    assert handler._get_model(prefs_empty) == "fallback-model"


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
    from mcp.types import SamplingMessage

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
    from mcp.types import SamplingMessage

    msgs = _convert_messages_to_google_genai_content(
        messages=[
            SamplingMessage(
                role="user",
                content=ToolResultContent(
                    type="tool_result",
                    toolUseId="get_weather_123",
                    content=[TextContent(type="text", text="Sunny, 72°F")],
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
    from mcp.types import SamplingMessage

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
