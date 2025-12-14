"""Sampling types and helper functions for FastMCP servers."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic

from mcp.types import (
    ClientCapabilities,
    CreateMessageResult,
    CreateMessageResultWithTools,
    ModelHint,
    ModelPreferences,
    SamplingCapability,
    SamplingMessage,
    SamplingToolsCapability,
    TextContent,
    ToolChoice,
    ToolResultContent,
    ToolUseContent,
)
from mcp.types import CreateMessageRequestParams as SamplingParams
from mcp.types import Tool as SDKTool
from typing_extensions import TypeVar

from fastmcp.exceptions import ToolError
from fastmcp.server.sampling.sampling_tool import SamplingTool
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from fastmcp.server.context import Context

ResultT = TypeVar("ResultT", default=str)


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
