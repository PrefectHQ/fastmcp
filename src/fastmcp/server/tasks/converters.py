"""SEP-1686 task result converters.

Converts raw task return values to MCP result types.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import mcp.types
import pydantic_core

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP


async def convert_tool_result(
    server: FastMCP, raw_value: Any, tool_name: str, client_task_id: str
) -> mcp.types.CallToolResult:
    """Convert raw tool return value to MCP CallToolResult.

    Replicates the serialization logic from tool.run() to properly handle
    output_schema, structured content, etc.

    Args:
        server: FastMCP server instance
        raw_value: The raw return value from user's tool function
        tool_name: Name of the tool (to get output_schema and serializer)
        client_task_id: Client task ID for related-task metadata

    Returns:
        CallToolResult with properly formatted content and structured content
    """
    # Import here to avoid circular import:
    # tools/tool.py -> tasks/config.py -> tasks/__init__.py -> converters.py -> tools/tool.py
    from fastmcp.tools.tool import ToolResult, _convert_to_content

    # Get the tool to access its configuration
    tool = await server.get_tool(tool_name)

    # Build related-task metadata
    related_task_meta = {
        "modelcontextprotocol.io/related-task": {
            "taskId": client_task_id,
        }
    }

    # If raw value is already ToolResult, use it directly
    if isinstance(raw_value, ToolResult):
        mcp_result = raw_value.to_mcp_result()
        if isinstance(mcp_result, mcp.types.CallToolResult):
            # Add metadata
            mcp_result._meta = related_task_meta
            return mcp_result
        elif isinstance(mcp_result, tuple):
            content, structured_content = mcp_result
            return mcp.types.CallToolResult(
                content=content,
                structuredContent=structured_content,
                _meta=related_task_meta,
            )
        else:
            return mcp.types.CallToolResult(content=mcp_result, _meta=related_task_meta)

    # Convert raw value to content blocks
    unstructured_result = _convert_to_content(raw_value, serializer=tool.serializer)

    # Handle structured content creation (same logic as tool.run())
    structured_content = None

    if tool.output_schema is None:
        # Try to serialize as dict for structured content
        try:
            sc = pydantic_core.to_jsonable_python(raw_value)
            if isinstance(sc, dict):
                structured_content = sc
        except pydantic_core.PydanticSerializationError:
            pass
    else:
        # Has output_schema - convert to JSON-able types
        jsonable_value = pydantic_core.to_jsonable_python(raw_value)
        wrap_result = tool.output_schema.get("x-fastmcp-wrap-result")
        structured_content = (
            {"result": jsonable_value} if wrap_result else jsonable_value
        )

    return mcp.types.CallToolResult(
        content=unstructured_result,
        structuredContent=structured_content,
        _meta=related_task_meta,
    )


async def convert_prompt_result(
    server: FastMCP, raw_value: Any, prompt_name: str, client_task_id: str
) -> mcp.types.GetPromptResult:
    """Convert raw prompt return value to MCP GetPromptResult.

    The user function returns raw values (strings, dicts, lists) that need
    to be converted to PromptMessage objects.

    Args:
        server: FastMCP server instance
        raw_value: The raw return value from user's prompt function
        prompt_name: Name of the prompt
        client_task_id: Client task ID for related-task metadata

    Returns:
        GetPromptResult with properly formatted messages
    """
    from fastmcp.prompts.prompt import PromptMessage

    # Get the prompt for metadata
    prompt = await server.get_prompt(prompt_name)

    # Normalize to list
    if not isinstance(raw_value, list | tuple):
        raw_value = [raw_value]

    # Convert to PromptMessages
    messages: list[mcp.types.PromptMessage] = []
    for msg in raw_value:
        if isinstance(msg, PromptMessage):
            # PromptMessage is imported from mcp.types - use directly
            messages.append(msg)
        elif isinstance(msg, str):
            messages.append(
                mcp.types.PromptMessage(
                    role="user",
                    content=mcp.types.TextContent(type="text", text=msg),
                )
            )
        elif isinstance(msg, dict):
            messages.append(mcp.types.PromptMessage.model_validate(msg))
        else:
            raise ValueError(f"Invalid message type: {type(msg)}")

    return mcp.types.GetPromptResult(
        description=prompt.description or "",
        messages=messages,
        _meta={
            "modelcontextprotocol.io/related-task": {
                "taskId": client_task_id,
            }
        },
    )


async def convert_resource_result(
    server: FastMCP, raw_value: Any, uri: str, client_task_id: str
) -> dict[str, Any]:
    """Convert raw resource return value to MCP resource contents dict.

    Args:
        server: FastMCP server instance
        raw_value: The raw return value from user's resource function (str or bytes)
        uri: Resource URI (for the contents response)
        client_task_id: Client task ID for related-task metadata

    Returns:
        Dict with 'contents' key containing list of resource contents
    """
    # Build related-task metadata
    related_task_meta = {
        "modelcontextprotocol.io/related-task": {
            "taskId": client_task_id,
        }
    }

    # Resources return str or bytes directly
    if isinstance(raw_value, str):
        return {
            "contents": [
                {
                    "uri": uri,
                    "text": raw_value,
                    "mimeType": "text/plain",
                }
            ],
            "_meta": related_task_meta,
        }
    elif isinstance(raw_value, bytes):
        return {
            "contents": [
                {
                    "uri": uri,
                    "blob": base64.b64encode(raw_value).decode(),
                    "mimeType": "application/octet-stream",
                }
            ],
            "_meta": related_task_meta,
        }
    else:
        # Fallback: convert to JSON string
        return {
            "contents": [
                {
                    "uri": uri,
                    "text": json.dumps(raw_value),
                    "mimeType": "application/json",
                }
            ],
            "_meta": related_task_meta,
        }
