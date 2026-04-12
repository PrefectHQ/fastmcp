"""FastMCP conformance test server.

Registers the exact tools, resources, and prompts expected by the
MCP conformance test suite (https://github.com/modelcontextprotocol/conformance).
"""

import asyncio
import base64
import json
import sys
from enum import Enum as PyEnum

import mcp.types
from mcp.types import EmbeddedResource, ImageContent, TextContent
from pydantic import AnyUrl, BaseModel, Field

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.prompts import Message
from fastmcp.server.context import Context
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.utilities.types import Audio, Image

# Minimal 1x1 red PNG for image tests (89 bytes)
_1X1_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg=="
)

# Minimal valid WAV: 16-bit mono PCM, 44100 Hz, single silent sample
_SILENT_WAV = (
    b"RIFF"
    + (38).to_bytes(4, "little")
    + b"WAVEfmt "
    + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little")  # PCM
    + (1).to_bytes(2, "little")  # mono
    + (44100).to_bytes(4, "little")  # sample rate
    + (88200).to_bytes(4, "little")  # byte rate
    + (2).to_bytes(2, "little")  # block align
    + (16).to_bytes(2, "little")  # bits per sample
    + b"data"
    + (2).to_bytes(4, "little")
    + (0).to_bytes(2, "little")  # one silent sample
)

server = FastMCP("conformance-test-server", dereference_schemas=False)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@server.tool(name="test_simple_text")
async def test_simple_text() -> str:
    """A simple text tool for conformance testing."""
    return "This is a simple text response for testing."


@server.tool(name="test_image_content")
async def test_image_content() -> Image:
    """Returns a PNG image."""
    return Image(data=_1X1_PNG, format="png")


@server.tool(name="test_audio_content")
async def test_audio_content() -> Audio:
    """Returns WAV audio."""
    return Audio(data=_SILENT_WAV, format="wav")


@server.tool(name="test_embedded_resource")
async def test_embedded_resource() -> list:
    """Returns an embedded resource."""
    return [
        EmbeddedResource(
            type="resource",
            resource=mcp.types.TextResourceContents(
                uri=AnyUrl("test://embedded-resource"),
                mimeType="text/plain",
                text="This is an embedded resource content.",
            ),
        )
    ]


@server.tool(name="test_multiple_content_types")
async def test_multiple_content_types() -> list:
    """Returns mixed text, image, and resource content."""
    return [
        TextContent(type="text", text="This is a text part of the response."),
        ImageContent(
            type="image",
            data=base64.b64encode(_1X1_PNG).decode(),
            mimeType="image/png",
        ),
        EmbeddedResource(
            type="resource",
            resource=mcp.types.TextResourceContents(
                uri=AnyUrl("test://mixed-content-resource"),
                mimeType="application/json",
                text='{"test":"data","value":123}',
            ),
        ),
    ]


@server.tool(name="test_error_handling")
async def test_error_handling() -> str:
    """Always returns an error."""
    raise ToolError("This tool intentionally returns an error for testing")


@server.tool(name="test_tool_with_logging")
async def test_tool_with_logging(ctx: Context) -> str:
    """Sends log notifications during execution."""
    await ctx.info("Tool execution started")
    await asyncio.sleep(0.05)
    await ctx.info("Tool processing data")
    await asyncio.sleep(0.05)
    await ctx.info("Tool execution completed")
    return "Logging test complete."


@server.tool(name="test_tool_with_progress")
async def test_tool_with_progress(ctx: Context) -> str:
    """Reports progress notifications."""
    await ctx.report_progress(0, 100)
    await asyncio.sleep(0.05)
    await ctx.report_progress(50, 100)
    await asyncio.sleep(0.05)
    await ctx.report_progress(100, 100)
    return "Progress test complete."


@server.tool(name="test_sampling")
async def test_sampling(prompt: str, ctx: Context) -> str:
    """Requests LLM sampling via the client."""
    result = await ctx.sample(
        messages=[prompt],
        result_type=str,
    )
    return f"Sampling result: {result}"


class _UserInfo(BaseModel):
    username: str
    email: str


@server.tool(name="test_elicitation")
async def test_elicitation(message: str, ctx: Context) -> str:
    """Requests user input via elicitation."""
    result = await ctx.elicit(message, _UserInfo)
    return f"Elicitation result: {result}"


class _UserStatus(str, PyEnum):
    active = "active"
    inactive = "inactive"
    pending = "pending"


class _DefaultsForm(BaseModel):
    name: str = Field(default="John Doe", description="User name")
    age: int = Field(default=30, description="User age")
    score: float = Field(default=95.5, description="User score")
    status: _UserStatus = Field(default=_UserStatus.active, description="User status")
    verified: bool = Field(default=True, description="Verification status")


@server.tool(name="test_elicitation_sep1034_defaults")
async def test_elicitation_sep1034_defaults(ctx: Context) -> str:
    """Tests elicitation with default values per SEP-1034."""
    result = await ctx.elicit(
        "Please review and update the form fields with defaults",
        _DefaultsForm,
    )
    return f"Elicitation completed: {result}"


@server.tool(name="test_elicitation_sep1330_enums")
async def test_elicitation_sep1330_enums(ctx: Context) -> str:
    """Tests elicitation with enum schema improvements per SEP-1330."""
    result = await ctx.session.elicit(
        message="Please select options from the enum fields",
        requestedSchema={
            "type": "object",
            "properties": {
                "untitledSingle": {
                    "type": "string",
                    "description": "Select one option",
                    "enum": ["option1", "option2", "option3"],
                },
                "titledSingle": {
                    "type": "string",
                    "description": "Select one option with titles",
                    "oneOf": [
                        {"const": "value1", "title": "First Option"},
                        {"const": "value2", "title": "Second Option"},
                        {"const": "value3", "title": "Third Option"},
                    ],
                },
                "legacyEnum": {
                    "type": "string",
                    "description": "Select one option (legacy)",
                    "enum": ["opt1", "opt2", "opt3"],
                    "enumNames": [
                        "Option One",
                        "Option Two",
                        "Option Three",
                    ],
                },
                "untitledMulti": {
                    "type": "array",
                    "description": "Select multiple options",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "string",
                        "enum": ["option1", "option2", "option3"],
                    },
                },
                "titledMulti": {
                    "type": "array",
                    "description": "Select multiple options with titles",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "anyOf": [
                            {"const": "value1", "title": "First Choice"},
                            {"const": "value2", "title": "Second Choice"},
                            {"const": "value3", "title": "Third Choice"},
                        ]
                    },
                },
            },
            "required": [],
        },
        related_request_id=ctx.request_id,
    )
    return f"Elicitation completed: action={result.action}, content={json.dumps(result.content or {})}"


async def _json_schema_2020_12_fn(
    name: str | None = None,
    address: dict | None = None,
) -> str:
    """Tool with JSON Schema 2020-12 features for conformance testing (SEP-1613)."""
    return f"JSON Schema 2020-12 tool called with: name={name}, address={address}"


server.add_tool(
    FunctionTool(
        fn=_json_schema_2020_12_fn,
        name="json_schema_2020_12_tool",
        description="Tool with JSON Schema 2020-12 features for conformance testing (SEP-1613)",
        parameters={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "$defs": {
                "address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                    },
                }
            },
            "properties": {
                "name": {"type": "string"},
                "address": {"$ref": "#/$defs/address"},
            },
            "additionalProperties": False,
        },
    )
)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@server.resource(
    "test://static-text",
    name="Static text resource",
    mime_type="text/plain",
)
async def static_text_resource() -> str:
    """Returns static text content."""
    return "This is the content of the static text resource."


@server.resource(
    "test://static-binary",
    name="Static binary resource",
    mime_type="image/png",
)
async def static_binary_resource() -> bytes:
    """Returns a binary PNG image."""
    return _1X1_PNG


@server.resource(
    "test://template/{id}/data",
    name="Template resource",
    mime_type="application/json",
)
async def template_resource(id: str) -> str:
    """Returns JSON data with the template parameter substituted."""
    return json.dumps({"id": id, "templateTest": True, "data": f"Data for ID: {id}"})


@server.resource(
    "test://watched-resource",
    name="Watched resource",
    mime_type="text/plain",
)
async def watched_resource() -> str:
    """A resource that supports subscriptions."""
    return "Watched resource content."


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@server.prompt(name="test_simple_prompt")
async def test_simple_prompt() -> str:
    """A simple prompt for conformance testing."""
    return "This is a simple prompt for testing."


@server.prompt(name="test_prompt_with_arguments")
async def test_prompt_with_arguments(arg1: str, arg2: str) -> str:
    """A prompt that accepts arguments."""
    return f"Prompt with arguments: arg1='{arg1}', arg2='{arg2}'"


@server.prompt(name="test_prompt_with_embedded_resource")
async def test_prompt_with_embedded_resource(resourceUri: str) -> list:
    """A prompt that returns an embedded resource."""
    return [
        Message(
            EmbeddedResource(
                type="resource",
                resource=mcp.types.TextResourceContents(
                    uri=AnyUrl(resourceUri),
                    mimeType="text/plain",
                    text=f"Content of resource {resourceUri}",
                ),
            )
        ),
    ]


@server.prompt(name="test_prompt_with_image")
async def test_prompt_with_image() -> list:
    """A prompt that returns an image."""
    return [
        Message(
            ImageContent(
                type="image",
                data=base64.b64encode(_1X1_PNG).decode(),
                mimeType="image/png",
            )
        ),
        Message("Please analyze the image above."),
    ]


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server.run(transport="streamable-http", host="127.0.0.1", port=port)
