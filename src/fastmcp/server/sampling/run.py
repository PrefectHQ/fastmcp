"""Sampling types and helper functions for FastMCP servers."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, cast

from mcp.types import (
    ClientCapabilities,
    CreateMessageResult,
    CreateMessageResultWithTools,
    ModelHint,
    ModelPreferences,
    SamplingCapability,
    SamplingMessage,
    SamplingMessageContentBlock,
    SamplingToolsCapability,
    TextContent,
    ToolChoice,
    ToolResultContent,
    ToolUseContent,
)
from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import Tool as SDKTool
from pydantic import ValidationError
from typing_extensions import TypeVar

from fastmcp import settings
from fastmcp.exceptions import ToolError
from fastmcp.server.sampling.sampling_tool import SamplingTool
from fastmcp.utilities.json_schema import compress_schema
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import get_cached_typeadapter

logger = get_logger(__name__)

if TYPE_CHECKING:
    from fastmcp.server.context import Context

ResultT = TypeVar("ResultT")

# Simplified tool choice type - just the mode string instead of the full MCP object
ToolChoiceOption = Literal["auto", "required", "none"]


@dataclass
class SamplingResult(Generic[ResultT]):
    """Result of a sampling operation.

    Attributes:
        text: The text representation of the result (raw text or JSON for structured).
        result: The typed result (str for text, parsed object for structured output).
        history: All messages exchanged during sampling.
    """

    text: str | None
    result: ResultT
    history: list[SamplingMessage]


@dataclass
class SampleStep:
    """Result of a single sampling call.

    Represents what the LLM returned in this step plus the message history.
    """

    response: CreateMessageResult | CreateMessageResultWithTools
    history: list[SamplingMessage]

    @property
    def is_tool_use(self) -> bool:
        """True if the LLM is requesting tool execution."""
        if isinstance(self.response, CreateMessageResultWithTools):
            return self.response.stopReason == "toolUse"
        return False

    @property
    def text(self) -> str | None:
        """Extract text from the response, if available."""
        content = self.response.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, TextContent):
                    return block.text
            return None
        elif isinstance(content, TextContent):
            return content.text
        return None

    @property
    def tool_calls(self) -> list[ToolUseContent]:
        """Get the list of tool calls from the response."""
        content = self.response.content
        if isinstance(content, list):
            return [c for c in content if isinstance(c, ToolUseContent)]
        elif isinstance(content, ToolUseContent):
            return [content]
        return []


def _parse_model_preferences(
    model_preferences: ModelPreferences | str | list[str] | None,
) -> ModelPreferences | None:
    """Convert model preferences to ModelPreferences object."""
    if model_preferences is None:
        return None
    elif isinstance(model_preferences, ModelPreferences):
        return model_preferences
    elif isinstance(model_preferences, str):
        return ModelPreferences(hints=[ModelHint(name=model_preferences)])
    elif isinstance(model_preferences, list):
        if not all(isinstance(h, str) for h in model_preferences):
            raise ValueError("All elements of model_preferences list must be strings.")
        return ModelPreferences(hints=[ModelHint(name=h) for h in model_preferences])
    else:
        raise ValueError(
            "model_preferences must be one of: ModelPreferences, str, list[str], or None."
        )


# --- Standalone functions for sample_step() ---


def determine_handler_mode(context: Context, needs_tools: bool) -> bool:
    """Determine whether to use fallback handler or client for sampling.

    Args:
        context: The MCP context.
        needs_tools: Whether the sampling request requires tool support.

    Returns:
        True if fallback handler should be used, False to use client.

    Raises:
        ValueError: If client lacks required capability and no fallback configured.
    """
    fastmcp = context.fastmcp
    session = context.session

    # Check what capabilities the client has
    has_sampling = session.check_client_capability(
        capability=ClientCapabilities(sampling=SamplingCapability())
    )
    has_tools_capability = session.check_client_capability(
        capability=ClientCapabilities(
            sampling=SamplingCapability(tools=SamplingToolsCapability())
        )
    )

    if fastmcp.sampling_handler_behavior == "always":
        if fastmcp.sampling_handler is None:
            raise ValueError(
                "sampling_handler_behavior is 'always' but no handler configured"
            )
        return True
    elif fastmcp.sampling_handler_behavior == "fallback":
        client_sufficient = has_sampling and (not needs_tools or has_tools_capability)
        if not client_sufficient:
            if fastmcp.sampling_handler is None:
                if needs_tools and has_sampling and not has_tools_capability:
                    raise ValueError(
                        "Client does not support sampling with tools. "
                        "The client must advertise the sampling.tools capability."
                    )
                raise ValueError("Client does not support sampling")
            return True
    elif fastmcp.sampling_handler_behavior is not None:
        raise ValueError(
            f"Invalid sampling_handler_behavior: {fastmcp.sampling_handler_behavior!r}. "
            "Must be 'always', 'fallback', or None."
        )
    elif not has_sampling:
        raise ValueError("Client does not support sampling")
    elif needs_tools and not has_tools_capability:
        raise ValueError(
            "Client does not support sampling with tools. "
            "The client must advertise the sampling.tools capability."
        )

    return False


async def call_sampling_handler(
    context: Context,
    messages: list[SamplingMessage],
    *,
    system_prompt: str | None,
    temperature: float | None,
    max_tokens: int,
    model_preferences: ModelPreferences | str | list[str] | None,
    sdk_tools: list[SDKTool] | None,
    tool_choice: ToolChoice | None,
) -> CreateMessageResult | CreateMessageResultWithTools:
    """Make LLM call using the fallback handler.

    Note: This function expects the caller (sample_step) to have validated that
    sampling_handler is set via determine_handler_mode(). The checks below are
    safeguards against internal misuse.
    """
    if context.fastmcp.sampling_handler is None:
        raise RuntimeError("sampling_handler is None")
    if context.request_context is None:
        raise RuntimeError("request_context is None")

    result = context.fastmcp.sampling_handler(
        messages,
        SamplingParams(
            systemPrompt=system_prompt,
            messages=messages,
            temperature=temperature,
            maxTokens=max_tokens,
            modelPreferences=_parse_model_preferences(model_preferences),
            tools=sdk_tools,
            toolChoice=tool_choice,
        ),
        context.request_context,
    )

    if inspect.isawaitable(result):
        result = await result

    # Convert string to CreateMessageResult
    if isinstance(result, str):
        return CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text=result),
            model="unknown",
            stopReason="endTurn",
        )

    return result


async def execute_tools(
    tool_calls: list[ToolUseContent],
    tool_map: dict[str, SamplingTool],
    mask_error_details: bool = False,
) -> list[ToolResultContent]:
    """Execute tool calls and return results.

    Args:
        tool_calls: List of tool use requests from the LLM.
        tool_map: Mapping from tool name to SamplingTool.
        mask_error_details: If True, mask detailed error messages from tool execution.
            When masked, only generic error messages are returned to the LLM.
            Tools can explicitly raise ToolError to bypass masking when they want
            to provide specific error messages to the LLM.

    Returns:
        List of tool result content blocks.
    """
    tool_results: list[ToolResultContent] = []

    for tool_use in tool_calls:
        tool = tool_map.get(tool_use.name)
        if tool is None:
            tool_results.append(
                ToolResultContent(
                    type="tool_result",
                    toolUseId=tool_use.id,
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: Unknown tool '{tool_use.name}'",
                        )
                    ],
                    isError=True,
                )
            )
        else:
            try:
                result_value = await tool.run(tool_use.input)
                tool_results.append(
                    ToolResultContent(
                        type="tool_result",
                        toolUseId=tool_use.id,
                        content=[TextContent(type="text", text=str(result_value))],
                    )
                )
            except ToolError as e:
                # ToolError is the escape hatch - always pass message through
                logger.exception(f"Error calling sampling tool '{tool_use.name}'")
                tool_results.append(
                    ToolResultContent(
                        type="tool_result",
                        toolUseId=tool_use.id,
                        content=[TextContent(type="text", text=str(e))],
                        isError=True,
                    )
                )
            except Exception as e:
                # Generic exceptions - mask based on setting
                logger.exception(f"Error calling sampling tool '{tool_use.name}'")
                if mask_error_details:
                    error_text = f"Error executing tool '{tool_use.name}'"
                else:
                    error_text = f"Error executing tool '{tool_use.name}': {e}"
                tool_results.append(
                    ToolResultContent(
                        type="tool_result",
                        toolUseId=tool_use.id,
                        content=[TextContent(type="text", text=error_text)],
                        isError=True,
                    )
                )

    return tool_results


# --- Helper functions for sampling ---


def prepare_messages(
    messages: str | Sequence[str | SamplingMessage],
) -> list[SamplingMessage]:
    """Convert various message formats to a list of SamplingMessage objects."""
    if isinstance(messages, str):
        return [
            SamplingMessage(
                content=TextContent(text=messages, type="text"), role="user"
            )
        ]
    else:
        return [
            SamplingMessage(content=TextContent(text=m, type="text"), role="user")
            if isinstance(m, str)
            else m
            for m in messages
        ]


def prepare_tools(
    tools: Sequence[SamplingTool | Callable[..., Any]] | None,
) -> list[SamplingTool] | None:
    """Convert tools to SamplingTool objects."""
    if tools is None:
        return None

    sampling_tools: list[SamplingTool] = []
    for t in tools:
        if isinstance(t, SamplingTool):
            sampling_tools.append(t)
        elif callable(t):
            sampling_tools.append(SamplingTool.from_function(t))
        else:
            raise TypeError(f"Expected SamplingTool or callable, got {type(t)}")

    return sampling_tools if sampling_tools else None


def extract_tool_calls(
    response: CreateMessageResult | CreateMessageResultWithTools,
) -> list[ToolUseContent]:
    """Extract tool calls from a response."""
    content = response.content
    if isinstance(content, list):
        return [c for c in content if isinstance(c, ToolUseContent)]
    elif isinstance(content, ToolUseContent):
        return [content]
    return []


def create_final_response_tool(result_type: type) -> SamplingTool:
    """Create a synthetic 'final_response' tool for structured output.

    This tool is used to capture structured responses from the LLM.
    The tool's schema is derived from the result_type.
    """
    type_adapter = get_cached_typeadapter(result_type)
    schema = type_adapter.json_schema()
    schema = compress_schema(schema, prune_titles=True)

    # Tool parameters must be object-shaped. Wrap primitives in {"value": <schema>}
    if schema.get("type") != "object":
        schema = {
            "type": "object",
            "properties": {"value": schema},
            "required": ["value"],
        }

    # The fn just returns the input as-is (validation happens in the loop)
    def final_response(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    return SamplingTool(
        name="final_response",
        description=(
            "Call this tool to provide your final response. "
            "Use this when you have completed the task and are ready to return the result."
        ),
        parameters=schema,
        fn=final_response,
    )


# --- Implementation functions for Context methods ---


async def sample_step_impl(
    context: Context,
    messages: str | Sequence[str | SamplingMessage],
    *,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_preferences: ModelPreferences | str | list[str] | None = None,
    tools: Sequence[SamplingTool | Callable[..., Any]] | None = None,
    tool_choice: ToolChoiceOption | str | None = None,
    auto_execute_tools: bool = True,
    mask_error_details: bool | None = None,
) -> SampleStep:
    """Implementation of Context.sample_step().

    Make a single LLM sampling call. This is a stateless function that makes
    exactly one LLM call and optionally executes any requested tools.
    """
    # Convert messages to SamplingMessage objects
    current_messages = prepare_messages(messages)

    # Convert tools to SamplingTools
    sampling_tools = prepare_tools(tools)
    sdk_tools: list[SDKTool] | None = (
        [t._to_sdk_tool() for t in sampling_tools] if sampling_tools else None
    )
    tool_map: dict[str, SamplingTool] = (
        {t.name: t for t in sampling_tools} if sampling_tools else {}
    )

    # Determine whether to use fallback handler or client
    use_fallback = determine_handler_mode(context, bool(sampling_tools))

    # Build tool choice
    effective_tool_choice: ToolChoice | None = None
    if tool_choice is not None:
        if tool_choice not in ("auto", "required", "none"):
            raise ValueError(
                f"Invalid tool_choice: {tool_choice!r}. "
                "Must be 'auto', 'required', or 'none'."
            )
        effective_tool_choice = ToolChoice(
            mode=cast(Literal["auto", "required", "none"], tool_choice)
        )

    # Effective max_tokens
    effective_max_tokens = max_tokens if max_tokens is not None else 512

    # Make the LLM call
    if use_fallback:
        response = await call_sampling_handler(
            context,
            current_messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            model_preferences=model_preferences,
            sdk_tools=sdk_tools,
            tool_choice=effective_tool_choice,
        )
    else:
        response = await context.session.create_message(
            messages=current_messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            model_preferences=_parse_model_preferences(model_preferences),
            tools=sdk_tools,
            tool_choice=effective_tool_choice,
            related_request_id=context.request_id,
        )

    # Check if this is a tool use response
    is_tool_use_response = (
        isinstance(response, CreateMessageResultWithTools)
        and response.stopReason == "toolUse"
    )

    # Always include the assistant response in history
    current_messages.append(SamplingMessage(role="assistant", content=response.content))

    # If not a tool use, return immediately
    if not is_tool_use_response:
        return SampleStep(response=response, history=current_messages)

    # If not executing tools, return with assistant message but no tool results
    if not auto_execute_tools:
        return SampleStep(response=response, history=current_messages)

    # Execute tools and add results to history
    step_tool_calls = extract_tool_calls(response)
    if step_tool_calls:
        effective_mask = (
            mask_error_details
            if mask_error_details is not None
            else settings.mask_error_details
        )
        tool_results: list[ToolResultContent] = await execute_tools(
            step_tool_calls, tool_map, mask_error_details=effective_mask
        )

        if tool_results:
            current_messages.append(
                SamplingMessage(
                    role="user",
                    content=cast(list[SamplingMessageContentBlock], tool_results),
                )
            )

    return SampleStep(response=response, history=current_messages)


async def sample_impl(
    context: Context,
    messages: str | Sequence[str | SamplingMessage],
    *,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model_preferences: ModelPreferences | str | list[str] | None = None,
    tools: Sequence[SamplingTool | Callable[..., Any]] | None = None,
    result_type: type[ResultT] | None = None,
    mask_error_details: bool | None = None,
) -> SamplingResult[ResultT]:
    """Implementation of Context.sample().

    Send a sampling request to the client and await the response. This method
    runs to completion automatically, executing a tool loop until the LLM
    provides a final text response.
    """
    # Safety limit to prevent infinite loops
    max_iterations = 100

    # Convert tools to SamplingTools
    sampling_tools = prepare_tools(tools)

    # Handle structured output with result_type
    tool_choice: str | None = None
    if result_type is not None and result_type is not str:
        final_response_tool = create_final_response_tool(result_type)
        sampling_tools = list(sampling_tools) if sampling_tools else []
        sampling_tools.append(final_response_tool)

        # Always require tool calls when result_type is set - the LLM must
        # eventually call final_response (text responses are not accepted)
        tool_choice = "required"

    # Convert messages for the loop
    current_messages: str | Sequence[str | SamplingMessage] = messages

    for _iteration in range(max_iterations):
        step = await sample_step_impl(
            context,
            messages=current_messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            model_preferences=model_preferences,
            tools=sampling_tools,
            tool_choice=tool_choice,
            mask_error_details=mask_error_details,
        )

        # Check for final_response tool call for structured output
        if result_type is not None and result_type is not str and step.is_tool_use:
            for tool_call in step.tool_calls:
                if tool_call.name == "final_response":
                    # Validate and return the structured result
                    type_adapter = get_cached_typeadapter(result_type)

                    # Unwrap if we wrapped primitives (non-object schemas)
                    input_data = tool_call.input
                    original_schema = compress_schema(
                        type_adapter.json_schema(), prune_titles=True
                    )
                    if (
                        original_schema.get("type") != "object"
                        and isinstance(input_data, dict)
                        and "value" in input_data
                    ):
                        input_data = input_data["value"]

                    try:
                        validated_result = type_adapter.validate_python(input_data)
                        text = json.dumps(
                            type_adapter.dump_python(validated_result, mode="json")
                        )
                        return SamplingResult(
                            text=text,
                            result=validated_result,
                            history=step.history,
                        )
                    except ValidationError as e:
                        # Validation failed - add error as tool result
                        step.history.append(
                            SamplingMessage(
                                role="user",
                                content=[
                                    ToolResultContent(
                                        type="tool_result",
                                        toolUseId=tool_call.id,
                                        content=[
                                            TextContent(
                                                type="text",
                                                text=(
                                                    f"Validation error: {e}. "
                                                    "Please try again with valid data."
                                                ),
                                            )
                                        ],
                                        isError=True,
                                    )
                                ],
                            )
                        )

        # If not a tool use response, we're done
        if not step.is_tool_use:
            # For structured output, the LLM must use the final_response tool
            if result_type is not None and result_type is not str:
                raise RuntimeError(
                    f"Expected structured output of type {result_type.__name__}, "
                    "but the LLM returned a text response instead of calling "
                    "the final_response tool."
                )
            return SamplingResult(
                text=step.text,
                result=cast(ResultT, step.text if step.text else ""),
                history=step.history,
            )

        # Continue with the updated history
        current_messages = step.history

        # After first iteration, reset tool_choice to auto
        tool_choice = None

    raise RuntimeError(f"Sampling exceeded maximum iterations ({max_iterations})")
