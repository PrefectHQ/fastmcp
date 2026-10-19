"""FastMCP conformance test server.

Registers the exact tools, resources, and prompts expected by the
MCP conformance test suite (https://github.com/modelcontextprotocol/conformance).
"""

import asyncio
import base64
import json
import sys
from enum import Enum as PyEnum
from typing import Annotated

import mcp_types
import uvicorn
from mcp.shared.exceptions import MCPError
from mcp_types import (
    ClientCapabilities,
    Completion,
    EmbeddedResource,
    ImageContent,
    MissingRequiredClientCapabilityErrorData,
    PromptReference,
    TextContent,
)
from mcp_types.jsonrpc import MISSING_REQUIRED_CLIENT_CAPABILITY
from pydantic import BaseModel, Field

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.prompts import Message
from fastmcp.server.completions import CompletionValues
from fastmcp.server.context import Context
from fastmcp.server.event_store import EventStore
from fastmcp.tools.function_tool import FunctionTool
from fastmcp.utilities.tasks import TaskConfig
from fastmcp.utilities.types import Audio, Image
from fastmcp_tasks import TasksExtension

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


def require_client_capability(ctx: Context, capability: str) -> None:
    """Raise `-32021` unless the client declared *capability* on this request.

    SEP-2575 makes capability negotiation per-request: the client repeats its
    capabilities in each request's `_meta`, and a server that needs one the
    client did not declare must answer with a
    `MissingRequiredClientCapabilityError` whose `data.requiredCapabilities` is
    a `ClientCapabilities` object keyed by the missing capability.
    """
    client_params = ctx.session.client_params
    declared = client_params.capabilities if client_params else None
    if declared is not None and getattr(declared, capability, None) is not None:
        return
    data = MissingRequiredClientCapabilityErrorData(
        required_capabilities=ClientCapabilities.model_validate({capability: {}})
    )
    raise MCPError(
        code=MISSING_REQUIRED_CLIENT_CAPABILITY,
        message=f"Client did not declare the required {capability!r} capability",
        data=data.model_dump(by_alias=True, mode="json", exclude_none=True),
    )


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
            resource=mcp_types.TextResourceContents(
                uri="test://embedded-resource",
                mime_type="text/plain",
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
            mime_type="image/png",
        ),
        EmbeddedResource(
            type="resource",
            resource=mcp_types.TextResourceContents(
                uri="test://mixed-content-resource",
                mime_type="application/json",
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
    await asyncio.sleep(0.01)
    await ctx.info("Tool processing data")
    await asyncio.sleep(0.01)
    await ctx.info("Tool execution completed")
    return "Logging test complete."


@server.tool(name="test_tool_with_progress")
async def test_tool_with_progress(ctx: Context) -> str:
    """Reports progress notifications."""
    await ctx.report_progress(0, 100)
    await asyncio.sleep(0.01)
    await ctx.report_progress(50, 100)
    await asyncio.sleep(0.01)
    await ctx.report_progress(100, 100)
    return "Progress test complete."


@server.tool(name="test_sampling")
async def test_sampling(prompt: str, ctx: Context) -> str:
    """Requests LLM sampling via the client.

    `Context` has no `sample()` — server-initiated sampling is not part of
    FastMCP's server API. The handshake-era wire path is still supported and
    still shipped (the proxy relay uses it), so this fixture reaches the SDK
    session directly to keep the scenario covered.
    """
    result = await ctx.session.create_message(  # ty: ignore[deprecated]
        messages=[
            mcp_types.SamplingMessage(
                role="user",
                content=mcp_types.TextContent(type="text", text=prompt),
            )
        ],
        max_tokens=512,
        related_request_id=ctx.origin_request_id,
    )
    text = (
        result.content.text if isinstance(result.content, mcp_types.TextContent) else ""
    )
    return f"Sampling result: {text}"


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
        requested_schema={
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
                    "$anchor": "address",
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
            # SEP-2106 requires servers to pass composition and conditional
            # keywords through to the client untouched.
            "allOf": [
                {
                    "anyOf": [
                        {"required": ["name"]},
                        {"required": ["address"]},
                    ]
                }
            ],
            "if": {"required": ["address"]},
            "then": {"properties": {"name": {"minLength": 1}}},
            "else": {},
            "additionalProperties": False,
        },
    )
)


@server.tool(name="test_reconnection")
async def test_reconnection(ctx: Context) -> str:
    """Closes the POST stream mid-call so the client must resume (SEP-1699).

    The result is written after the stream is gone, so it can only reach the
    client through the event store on reconnect.
    """
    await ctx.report_progress(0, 100)
    await ctx.close_sse_stream()
    await asyncio.sleep(0.1)
    return "Reconnection test complete."


@server.tool(name="test_custom_headers")
async def test_custom_headers(
    message: Annotated[str, Field(json_schema_extra={"x-mcp-header": "Message"})],
) -> str:
    """Mirrors an argument into an `Mcp-Param-Message` header (SEP-2243).

    The annotation is what makes the header recognized; the transport compares
    the header against this argument before the tool ever runs.
    """
    return f"Received message: {message}"


@server.tool(name="test_missing_capability")
async def test_missing_capability(ctx: Context) -> str:
    """Requires the client to have declared the sampling capability (SEP-2575).

    A stateless server may not rely on a capability the client did not declare
    in this request's `io.modelcontextprotocol/clientCapabilities` `_meta`
    block, so an undeclared caller gets `-32021` rather than a tool result.
    """
    require_client_capability(ctx, "sampling")
    return "Client declared the sampling capability."


# ---------------------------------------------------------------------------
# Multi-round-trip input requests (SEP-2322)
#
# A guard component returns an `InputRequiredResult` naming what it needs; the
# client fulfils those requests and calls again, and the answers arrive on
# `ctx.input_responses` with any `ctx.request_state` echoed back. The framework
# seals and verifies `request_state`, so a tampered echo is rejected before a
# handler sees it.
# ---------------------------------------------------------------------------


def _elicit_request(message: str, field: str) -> mcp_types.ElicitRequest:
    """A single-field form elicitation for *field*."""
    return mcp_types.ElicitRequest(
        method="elicitation/create",
        params=mcp_types.ElicitRequestFormParams(
            message=message,
            requested_schema={
                "type": "object",
                "properties": {field: {"type": "string"}},
                "required": [field],
            },
        ),
    )


def _sampling_request(text: str, max_tokens: int) -> mcp_types.CreateMessageRequest:
    """A one-message sampling request."""
    return mcp_types.CreateMessageRequest(
        method="sampling/createMessage",
        params=mcp_types.CreateMessageRequestParams(
            messages=[
                mcp_types.SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text=text),
                )
            ],
            max_tokens=max_tokens,
        ),
    )


def _elicited_field(responses: mcp_types.InputResponses, key: str, field: str) -> str:
    """The accepted value of *field* from the elicitation answered under *key*."""
    answer = responses[key]
    if not isinstance(answer, mcp_types.ElicitResult) or answer.content is None:
        return ""
    return str(answer.content.get(field, ""))


@server.tool(name="test_input_required_result_elicitation")
async def test_input_required_result_elicitation(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """Asks the client one elicitation question, then greets the answer.

    A retry whose `inputResponses` omit the key is re-asked rather than
    errored: the answer is still missing, so the honest result is the same
    request again.
    """
    responses = ctx.input_responses
    if responses is None or "user_name" not in responses:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={"user_name": _elicit_request("What is your name?", "name")},
        )
    return f"Hello, {_elicited_field(responses, 'user_name', 'name')}!"


@server.tool(name="test_input_required_result_sampling")
async def test_input_required_result_sampling(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """Asks the client to sample an answer, then echoes the sampled text."""
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "capital_question": _sampling_request(
                    "What is the capital of France?", 100
                )
            },
        )
    answer = responses["capital_question"]
    text = ""
    if isinstance(answer, mcp_types.CreateMessageResult) and isinstance(
        answer.content, TextContent
    ):
        text = answer.content.text
    return f"Sampling result: {text}"


@server.tool(name="test_input_required_result_list_roots")
async def test_input_required_result_list_roots(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """Asks the client for its roots, then reports them back."""
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "client_roots": mcp_types.ListRootsRequest(method="roots/list")
            },
        )
    answer = responses["client_roots"]
    roots = (
        [str(root.uri) for root in answer.roots]
        if isinstance(answer, mcp_types.ListRootsResult)
        else []
    )
    return f"Client roots: {', '.join(roots)}"


@server.tool(name="test_input_required_result_request_state")
async def test_input_required_result_request_state(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """Carries opaque state across the round trip and confirms it came back."""
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "confirm": mcp_types.ElicitRequest(
                    method="elicitation/create",
                    params=mcp_types.ElicitRequestFormParams(
                        message="Please confirm",
                        requested_schema={
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                        },
                    ),
                )
            },
            request_state="conformance-state-v1",
        )
    if ctx.request_state != "conformance-state-v1":
        raise ToolError("requestState was not echoed back intact")
    return "state-ok: requestState round-tripped"


@server.tool(name="test_input_required_result_multiple_inputs")
async def test_input_required_result_multiple_inputs(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """Asks for elicitation, sampling, and roots in a single round."""
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "user_name": _elicit_request("What is your name?", "name"),
                "greeting": _sampling_request("Generate a greeting", 50),
                "client_roots": mcp_types.ListRootsRequest(method="roots/list"),
            },
            request_state="conformance-multi-v1",
        )
    name = _elicited_field(responses, "user_name", "name")
    return f"Collected {len(responses)} responses for {name}"


@server.tool(name="test_input_required_result_multi_round")
async def test_input_required_result_multi_round(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """Asks two dependent questions across three rounds."""
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "step1": _elicit_request("Step 1: What is your name?", "name")
            },
            request_state="round-1",
        )
    if "step1" in responses:
        name = _elicited_field(responses, "step1", "name")
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "step2": _elicit_request(
                    "Step 2: What is your favorite color?", "color"
                )
            },
            request_state=f"round-2:{name}",
        )
    color = _elicited_field(responses, "step2", "color")
    name = (ctx.request_state or "round-2:").split(":", 1)[1]
    return f"{name} likes {color}"


@server.tool(name="test_input_required_result_tampered_state")
async def test_input_required_result_tampered_state(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """Round-trips sealed state so a tampered echo is rejected by the framework."""
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "confirm": _elicit_request("Please confirm", "confirmation")
            },
            request_state="sealed-state-v1",
        )
    return f"Accepted state: {ctx.request_state}"


@server.tool(name="test_input_required_result_capabilities")
async def test_input_required_result_capabilities(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """Asks only for the input methods this client declared it can answer."""
    responses = ctx.input_responses
    if responses is not None:
        return f"Collected {len(responses)} responses"

    client_params = ctx.session.client_params
    declared = client_params.capabilities if client_params else None
    requests: dict[str, mcp_types.InputRequest] = {}
    if declared is not None and declared.sampling is not None:
        requests["capital_question"] = _sampling_request(
            "What is the capital of France?", 100
        )
    if declared is not None and declared.elicitation is not None:
        requests["user_name"] = _elicit_request("What is your name?", "name")
    if declared is not None and declared.roots is not None:
        requests["client_roots"] = mcp_types.ListRootsRequest(method="roots/list")
    if not requests:
        return "Client declared no input capabilities"
    return mcp_types.InputRequiredResult(
        result_type="input_required",
        input_requests=requests,
    )


# ---------------------------------------------------------------------------
# Background tasks (SEP-2663)
#
# The tasks extension is what turns `task=`-declared tools into background
# work; registering it also advertises `io.modelcontextprotocol/tasks` under
# `capabilities.extensions` and gates the `tasks/*` methods on negotiation.
# The in-memory Docket backend keeps the fixture to a single process.
# ---------------------------------------------------------------------------

server.add_extension(TasksExtension(url="memory://"))


@server.tool(name="greet")
async def greet(name: str) -> str:
    """A sync-only tool: never runs as a task."""
    return f"Hello, {name}!"


@server.tool(name="slow_compute", task=True)
async def slow_compute(seconds: float = 1.0, label: str = "") -> str:
    """Sleeps for *seconds*, so a cancel can land while it is still running."""
    await asyncio.sleep(seconds)
    return f"Computed {label} after {seconds} seconds"


@server.tool(name="failing_job", task=TaskConfig(mode="required"))
async def failing_job() -> str:
    """Reports a tool execution error: `completed` with `result.isError`.

    Registered `required` so a client that never negotiated the extension gets
    `-32021` rather than a synchronous run.
    """
    await asyncio.sleep(1)
    raise ToolError("This job intentionally fails for testing")


@server.tool(name="protocol_error_job", task=True)
async def protocol_error_job() -> str:
    """Raises a protocol-level error: `failed` with an inlined `error`."""
    raise MCPError(
        code=mcp_types.INTERNAL_ERROR,
        message="Protocol-level failure for testing",
    )


@server.tool(name="confirm_delete", task=True)
async def confirm_delete(
    filename: str, ctx: Context
) -> str | mcp_types.InputRequiredResult:
    """Parks the task on one elicitation before doing the (pretend) deletion."""
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "confirm": _elicit_request(
                    f"Confirm deletion of {filename}?", "confirmation"
                )
            },
        )
    answer = _elicited_field(responses, "confirm", "confirmation")
    return f"Deleted {filename}: {answer}"


@server.tool(name="multi_input", task=True)
async def multi_input(ctx: Context) -> str | mcp_types.InputRequiredResult:
    """Parks the task on two elicitations at once, so they can be answered separately."""
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "first": _elicit_request("First question?", "first"),
                "second": _elicit_request("Second question?", "second"),
            },
        )
    first = _elicited_field(responses, "first", "first")
    second = _elicited_field(responses, "second", "second")
    return f"Answers: {first}, {second}"


@server.tool(name="test_tool_with_task", task=TaskConfig(mode="required"))
async def test_tool_with_task(ctx: Context) -> str | mcp_types.InputRequiredResult:
    """Gathers input over MRTR, then escalates the final round to a task.

    The composition is the point: round 1 is a plain `InputRequiredResult`
    with no `taskId`, and the round that actually does the work becomes a
    `CreateTaskResult` because the tool requires task execution.
    """
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={"user_name": _elicit_request("What is your name?", "name")},
        )
    return f"Task completed for {_elicited_field(responses, 'user_name', 'name')}"


# ---------------------------------------------------------------------------
# Completions
# ---------------------------------------------------------------------------

_PROMPT_ARG_COMPLETIONS = ["paris", "park", "party"]


@server.completion
async def complete(
    ref: mcp_types.PromptReference | mcp_types.ResourceTemplateReference,
    argument: mcp_types.CompletionArgument,
    context: mcp_types.CompletionContext | None,
) -> CompletionValues:
    """Suggests values for `test_prompt_with_arguments` arguments."""
    if isinstance(ref, PromptReference) and ref.name == "test_prompt_with_arguments":
        matches = [
            value
            for value in _PROMPT_ARG_COMPLETIONS
            if value.startswith(argument.value)
        ]
        return Completion(values=matches, total=len(matches), has_more=False)
    return None


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
                resource=mcp_types.TextResourceContents(
                    uri=resourceUri,
                    mime_type="text/plain",
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
                mime_type="image/png",
            )
        ),
        Message("Please analyze the image above."),
    ]


@server.prompt(name="test_input_required_result_prompt")
async def test_input_required_result_prompt(
    ctx: Context,
) -> str | mcp_types.InputRequiredResult:
    """A prompt that gathers its context by elicitation before rendering.

    `InputRequiredResult` is universal — it is a result type, not a tools/call
    feature — so `prompts/get` can ask for input the same way a tool does.
    """
    responses = ctx.input_responses
    if responses is None:
        return mcp_types.InputRequiredResult(
            result_type="input_required",
            input_requests={
                "user_context": _elicit_request(
                    "What context should the prompt use?", "context"
                )
            },
        )
    context_value = _elicited_field(responses, "user_context", "context")
    return f"Prompt rendered with context: {context_value}"


MCP_PATH = "/mcp"


def build_app():
    """The ASGI app the conformance suite is run against.

    Shared by the pytest fixture and the `__main__` entry point so both exercise
    the same configuration. The event store is what makes SSE resumption work,
    which `test_reconnection` depends on; host/origin protection is a spec MUST
    for a localhost server without TLS or auth.
    """
    return server.http_app(
        transport="streamable-http",
        path=MCP_PATH,
        host_origin_protection=True,
        event_store=EventStore(),
        retry_interval=100,
    )


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
